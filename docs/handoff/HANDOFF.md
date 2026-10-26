# HANDOFF for the next AI (written 2026-10-21)

**The full list of what remains (tasks, follow-ups, business actions, definition of done) is in `BACKLOG.md` next to this file. Read both.**

Read this first, then `docs/handoff/PROGRAM.md`, `docs/handoff/DECISION-LEDGER-2026-10-19-finish.md` (the dated decision ledger, newest at the
bottom) and `docs/reviews/2026-10-20-stack-jury-synthesis.md`. Everything below is the state as of the end of the session that wrote it.

## 1. Who the owner is and how to work with them

- The owner is a solo developer in the Dominican Republic (email on the account). **Talk to them in Spanish; code, docs, commit messages and the bot/API UI stay English.**
- Quality bar (non-negotiable): senior big-tech level. Strict typing (basedpyright `all` + mypy `--strict`; the jury suggests dropping mypy: not yet decided), Python 3.15,
  **zero suppressions** (no `Any`, `cast`, `noqa`, `type: ignore`, pragmas), 100% branch coverage per package, TDD (tests first, record the red run).
- Never print `.env` values, tokens or key material. Never put secrets in chat. Never handle card/payment data.
- **Never kill processes by image name** (an agent once ran `taskkill /F /IM python.exe`; never again): only your own PIDs.
- **Port 8000 is reserved for another project.** Ports used here: api 8100, worker 8101, signer HTTP 8102, receiver 8103, signer gRPC 50051 (not published).
- **Leave the `ferreteria` containers alone** (another project; one was in a restart loop and is not ours).
- **Commit locally only. Never push, amend or rewrite history unless the owner asks.** Local `main` is at d2694bc and is ahead of `origin/main` (44d77a3); nothing has been pushed.
- Merge a branch into local `main` only after its final review (owner: "after the final review, when you finish everything").
- Do not modify system security settings. Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Honesty rules the owner values: say what is verified vs assumed, correct yourself when wrong (an earlier "I launched the plans" was false and was corrected), never claim a green gate you did not see.

## 2. What the product is

`C:\Users\brian\Desktop\ledger`: a Python 3.15 platform. First product: a Dominican Republic e-CF (DGII electronic invoicing) API. The owner's real ambition
(stated 2026-10-20): a full business suite in the class of Alegra / Odoo / SAP (accounting, inventory, POS, payroll, many countries). The jury's answer is in
section 7. Legal deadline for small taxpayers: **2026-11-15** (cannot be met as an authorized provider; aim at the late-adopter tail and software vendors).

## 3. Repo map (uv workspace)

- `packages/`: `kernel`, `platform` (the chassis: Postgres per-module schemas + roles + RLS, outbox/inbox, jobs with leases/fencing, idempotency, GCRA rate limit, outbound HTTP,
  blobs/S3 store, gRPC guard/server/client, runtime/serve), `testing` (`ledger_testing`: fixtures, fake stores, LocalStack, PKI), `fiscal-kit` (country-neutral fiscal contract).
- `modules/`: `identity`, `invoicing`, `fiscal`, `signer` (isolated process, ADR 0010).
- `countries/do` (+ `countries/do/testing`): regime, formats/XSDs, builders, DGII client, buyer client, state machine, numbering, channel `do-ecf-v1`, fake DGII.
- `apps/`: `api`, `worker`, `migrate`, `signer` (receiver app is in review, see section 5).
- `tools/devtools`: manifest-driven `ledger-devtools sync` / `check` (generates pyprojects, import-linter contracts, init.sql, OpenAPI). Commands: `uv sync --locked --all-packages`,
  `uv run ledger-devtools check`, `uv run poe sync`, `uv run poe check` (the full gate), `poe dead-src`, `poe proto-lint`, `poe proto-breaking --base <ref>`.
- Docs: `docs/superpowers/specs/2026-10-13-ledger-platform-v2-design.md` (the spec wins), `docs/adr/0001..0011`, plans in `docs/superpowers/plans/` (v2-m2b-signer, v2-m3b-fiscal: both contain
  dated "As built" and review notes per task: READ THEM, they carry binding decisions), reviews in `docs/reviews/`, runbooks in `docs/runbooks/`.
- Ledgers: `docs/handoff/PROGRAM.md` (program) and `docs/handoff/DECISION-LEDGER-2026-10-19-finish.md` (this run). Keep them updated with `echo "- date: ..." >>`.
- Auto-memory: `C:\Users\brian\.claude\projects\C--Users-brian-Desktop-shieldbot\memory\` (see its MEMORY.md).

## 4. The working process (SDD: subagent-driven development)

Per plan task: an implementer subagent (opus) in its **own git worktree + branch** (`git worktree add C:\Users\brian\Desktop\ledger-<x> -b feat/<name> feat/signer`) →
an independent opus **reviewer** (read-only, targeted tests only) → a fix round sent to the implementer with SendMessage and explicit rulings → gate green twice →
merge into the integration branch `feat/signer` (worktree `C:\Users\brian\Desktop\ledger-sg`) → `uv run ledger-devtools check`. Full gate on the integration branch every few merges.
- Subagents cannot write report files: reports go in their final message. Keep ledgers updated yourself.
- Max ~3-4 heavy gate-running jobs at once; gates take 10-15 min loaded. Use `LEDGER_TEST_JOBS=2`. Docker Desktop must be running
  (`C:\Users\brian\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`).
- Use UNIQUE gate log names in the scratchpad (outside the worktree: logs inside the repo trip `typos`). Scratchpad:
  `C:\Users\brian\AppData\Local\Temp\claude\C--Users-brian-Desktop-shieldbot\7fd59081-7026-43cc-b8a5-61b41b7323ca\scratchpad\`.
- Weekly/session usage limits kill running subagents; their work is saved in the worktrees: resume with SendMessage ("continue in ledger-xx, check git status/log").
- Windows gotchas: edits can write CRLF (convert to LF before committing); pytest-timeout thread mode ends a hung test with `os._exit` and xdist reports "node down" (look for the
  `+++ Timeout +++` dump before blaming load); `git revert` creates commits without the trailer.
- Known flakes: `countries/do` DGII client timeout branch (fixed in Task 14 via asyncio.Timeout expiry), a docker xmlsec test, platform `test_grpc_guard_served`/auth keys under load,
  signer import tests racing wall clocks (hardened in signer Task 14). Rerun once and say so if only a known flake fails.

## 5. State of the work (2026-10-21)

**Integration branch `feat/signer` (worktree `ledger-sg`) HEAD 40b89e8.** Last full gate green at 5cd35a2 (and devtools check 0 after every later merge; a full gate on 40b89e8 was NOT run: run it first).
It contains local `main` (v2-M1, DGII spike, GitHub cloud work + 23-commit fix wave, invoicing) plus:

- **Signer (v2-M2b): Tasks 1-15 complete and merged.** Isolated module, custody (AES-256-GCM envelope, local-file KMS refused outside local/test), gRPC server/client with per-caller tokens,
  HTTP `/v1/certificates` (test-mode safety rule, base64 password header), `apps/signer`, compose, runbooks, final review `docs/reviews/2026-10-20-signer-final-review.md`.
  **Not production-ready:** blockers = AWS KMS adapter (F1), INDOTEL custody decision, TLS on the gRPC port (F3), egress proxy, deploy pipeline. Follow-ups F1-F11 sized in the final review.
- **Fiscal (v2-M3b) merged:** Tasks 1 (kit contract), 2 (XML/status in kit), 3 (outside consumers), 4 (blobs/ArchivePort), 5 (DO drafts/mapping), 6 (OutboundHttp, ChannelContext.auth/http),
  7+8 (builders/goldens all types, withholdings), 9 (ANECF/ACECF/ARECF, hardened inbound readers), 10 (state machine, numbering domain, RFCE map), 11 (`fiscal` module), 12 (invoicing issue policies,
  mirror, representation), 14+15 (DGII client per company, buyer client on SignerPort), 16 (S3 object-lock store + LocalStack), 17 (DO channel entry, schema `do_ecf`, ranges API), 18 (`Channel.check`/`issue`).
- **GitHub (private repo `balegre0/ledger`), pushed on 2026-10-21 at the owner's explicit request:** `main` (d2694bc + this handoff docs commit), `feat/signer` (integration branch, all merged work + these docs),
  `feat/m3b-receiver` (Task 13, committed, unreviewed). **Task 19's work was NOT pushed: it is uncommitted in the local worktree `ledger-m19` (an agent was still working on it).** All other `feat/*` branches are already merged into `feat/signer`.
  After this push, further pushes still need the owner's permission each time. `.superpowers/` is git-ignored (local ledgers); the tracked copies of the ledgers live in `docs/handoff/`.
- **UPDATE 2026-10-25 (CURRENT STATE; it supersedes the 2026-10-21 notes below wherever they differ).** The owner asked to push everything to `main`:
  - `main` contains Task 13 (receiver role, `apps/receiver`), merged with Tasks 14-18 and the signer app: conflicts resolved, the receiver wired to the signer and to `LEDGER_DO_ECF_TRUSTED_ROOTS`, `tokens.sh` writes `receiver.env`, and the DO assembly refuses a receiver without trusted roots.
  - Two timing-sensitive platform tests were hardened (the migration lock wait asserts at least 0.5 s because Docker Desktop's VM clock runs fast against the host's; three inbox lease tests call `bench.make_due()` before claiming). **The full gate was green twice on this tree** (2026-10-25, Docker up, default jobs). Task 13 has not had an independent review yet.
  - **PR #3 (`feat/m3b-receiver` into `main`) is obsolete: its content is in `main`. Close it without merging.** The branch `feat/everything` equals `main`.
  - **UPDATE 2026-10-25 (later): Task 19 is now merged into LOCAL `main` (fast-forward to 7b01779) after the re-review's small fixes and two green full gates; it is NOT pushed (GitHub `main` is at 2f9e5d9, 5 commits behind). Remaining Task 19 follow-ups are the binding items for Tasks 20, 21, 27, 28, 31 and Owner item 13. Pushing needs the owner's say-so.**
  - **Task 19 (`do_ecf.advance`, the job that submits to DGII), history:** it was in review-fixed state on the branch `feat/m3b-advance-final` (worktree `ledger-t19`). The snapshot (`wip/m3b-advance-snapshot`, b0da2d2) was merged onto `main` there (52a3d47); an independent review returned CHANGES REQUIRED (never-resend sound, 9 mutations verified); the fix round closed the adopt → "No encontrado" loop once the 24 h window is closed, added the test for the mark guard, and recorded the binding items for Tasks 20, 21, 27, 28 and 31 and Owner item 13 (a woken job past its 15-day deadline dies) in the plan's Task 19 as-built note and in BACKLOG.md. Next: a re-review of the fix commits, then merge into `main` (Tasks 17, 18 and 19 are already together there). The worktree `ledger-m19` and the snapshot branch are superseded. Gate (2026-10-25): on the first fix round's tree, three runs: run 1 exit 1, its only failure the known gRPC flake `test_grpc_guard_served.py::test_a_method_outside_the_allowlist_is_permission_denied[stream-stream]` (INTERNAL from gRPC Core under load; the platform package is untouched by this branch), runs 2 and 3 green (exit 0). The re-review asked for small changes (a poll's 0 waits while the window is open, `En Proceso` leaves `track_id_since` unset: both now tested and mutation-checked; wording); on that tree two runs, both green (exit 0).
  - **Task 13 (receiver) reviewed and fixed (2026-10-25):** the independent review returned CHANGES REQUIRED (no Critical; the receiver is correctly built and ready for Task 22). The fix round is on the branch `feat/m3b-receiver-fix` (worktree `ledger-r13`, from `main` 6f0ab2b), NOT merged and NOT pushed: the receiver's public edge serves no OpenAPI document and answers 404 to `/livez`, `/readyz`, `/metrics` asked through a proxy (any `X-Forwarded-For`), it reads and parses its trusted roots at start (an unreadable file stops it without naming the path), IPv6 clients are limited by their /64, invalid `X-Forwarded-For` entries fall back to the peer, a deployed receiver with no proxy declared warns, compose no longer gives it Zitadel's env file, CI's smoke checks it serves no api route. The plan's Task 13 review note has the details; the three owner notes (SignSeed, a narrower DB login, the `/archive` volume) are in BACKLOG.md. Gate (2026-10-25, Docker up, default jobs): two full runs on this branch, both green (exit 0). Next: re-review if wanted, then merge into `main`.
  - Tooling on this machine: Docker Desktop must be running for the gate (`proto-lint` and the database tests need it); `uv` is in `C:/Users/brian/.local/bin`, which a bash session must add to `PATH`.
  - The owner said: continue ONLY what was already started, and wait for their green light before continuing the project. **Do not start Tasks 20 onward until the owner says so.**
  - **UPDATE 2026-10-25 (latest): green light given.** After the architecture review
    (`docs/reviews/2026-10-25-architecture-review/VERDICT.md`, which puts the e-CF engine and its certification path
    first), the owner asked to continue every remaining task. Tasks 20, 21 and 22 started in parallel worktrees
    `ledger-m3b-t20`, `ledger-m3b-t21`, `ledger-m3b-t22` (branches `v2m3b/t20`..`t22` from `main` e95b082); Task 24 and
    then Task 23 follow, each through the SDD loop (implementer, independent review, fix round, gate twice, merge).
    Run `uv sync --locked --all-packages` in any fresh tree: without it the receiver app is missing from the venv and
    basedpyright fails on `apps/receiver/tests/unit/test_main.py`.
  - **UPDATE 2026-10-26 (latest): Tasks 20, 21, 22, 23, 24, 25 and the new Task 35 (the issuer's fiscal
    address, DireccionEmisor, with a dated spec amendment; the address is company-level, owner item 30) are merged
    into local `main` (5b3b4b8). Each was reviewed until APPROVED and its gate was green twice on the exact tree
    merged (every merge commit's tree equals its verified branch tree). do_ecf revisions run r0004 (receiver), r0005
    (delivery), r0006 (representation), r0007 (approvals); the worker pool is 78 and Postgres `max_connections`
    372. **Task 26 is merged too (7b2eef2; reviewed twice, APPROVED after 6536049): the fake DGII is complete, compose
    runs the stack, `poe e2e` is part of the gate, and the end-to-end suite covers issue → DGII → buyer delivery →
    commercial approval → PDF, the slow signer, a lost answer never resent, a real certificate event waking a parked
    document, and reception plus approval out. It found that the api must sign in the request (spec §3.3/§8.5): the
    signer now lets the api sign e-CF and RFCE only (ADR 0010 risks 8-9 amended; owner item 32), and plain http to a
    compose service name is allowed in local and test environments only.** Remaining CERT work: Task 27 (retire the
    spike CLI, runbooks and the alerts the reviews bound to it: dead `do_ecf.advance`/`deliver`/`approve` jobs, the
    stuck-pending invoice, the two delivery alerts) and Task 34 (final verification and whole-branch review). Owner
    items 5 and 13-33 in the plan are open questions for the owner. Follow-ups noted by the reviews (not blocking):
    Task 22's two untested `to_thread` hand-offs and its limiter-order test; Task 25's facade guard on fiscal's test
    channel; Task 26's plain-http case on the `fc` host only; the CI job's 30-minute limit. Pushed to GitHub `main`
    on the owner's request (2026-10-26).**
  - **UPDATE 2026-10-26: Task 22 is merged into local `main` (b1331c3; reviewed twice, APPROVED after its fix
    round 4b67429), with a devtools fix: the tree scan no longer enters a nested repository or worktree (another
    session's worktree under `.claude/worktrees/` had failed `ledger-devtools check`). Task 20 is APPROVED after its
    fix round (3193d5c) and merges next; Task 21 has a last fix (d89ed40) and merges after it, renumbering its
    revision to r0005; Task 24 is in its fix round. Each later task merges `main` into its branch first. Nothing
    pushed.**
  - **STATE when the usage limit hit (2026-10-25, late; superseded by the update above):**
    - Task 20 `v2m3b/t20` f497b43: gates 5 and 6 green; an independent review was running. Blocker it found:
      DGII requires `DireccionEmisor` but no module stores a company/establishment address (spec §16.4 deferred it
      "until a country needs it": DO does). Needs a spec amendment and a new task (identity stores the address,
      invoicing snapshots it into `SellerInfo`, fiscal maps it) before any real-channel issue can pass.
    - Task 21 `v2m3b/t21` 221c96b: review CHANGES REQUIRED (5 untested rules, 3 stuck-delivery paths, minors);
      fix round sent to its implementer, fix commit pending. Reviewer log: scratchpad `rv21-mutations.log`.
    - Task 22 `v2m3b/t22` ccb231a: review CHANGES REQUIRED, one Critical (any trusted taxpayer could approve or
      reject our e-CF: the ACECF buyer was never matched to the document's buyer) plus 4 Important; fix round sent,
      fix commit pending.
    - Task 24 `v2m3b/t24` (from 610d155): implemented, its gate was running; not reviewed.
    - Merge plan: Tasks 21 and 22 both add do_ecf revision `r0004`: the second to merge renumbers to `r0005`
      (reviewed safe). Recompute the worker pool from `PoolTooSmallError` after merging (Task 20 needs 50 at
      concurrency 1, Task 21 set 70 and `max_connections=400`). Then Task 23, 25, 26, 27, 34.
    - On resume: check each worktree's `git log`/`git status`; resume the agents or redo their last step; every
      task still needs its fix commit, two green gates, then merge into `main`.
- **UPDATE 2026-10-21 (late; partly superseded by the update above): the owner decided to stop pushing and continue locally.** What happened after the first push:
  - The owner merged PR #2 (`feat/signer` into `main`) on GitHub: GitHub `main` = be3b9c0 and contains everything up to M3b Task 18 and the signer, plus these docs. Local `main` was fast-forwarded to it. **PR #3 (`feat/m3b-receiver` into `main`) is premature and conflicting: close it without merging.**
  - **RECEIVER MERGE RESOLVED LOCALLY (2026-10-21, late):** branch `feat/everything` in worktree `ledger-final` now holds `main` + Task 13: commits 66ee121 (merge) and 50b7491 (fix-up: signer/roots wiring for the receiver, tokens.sh writes receiver.env, DO assembly refuses a receiver without LEDGER_DO_ECF_TRUSTED_ROOTS, CI smoke for the receiver). Conflicts were resolved keeping both sides and generated files regenerated; `uv lock --check` and `ledger-devtools check` pass; 48 import contracts hold (signer-custody covers ledger_app_receiver). Its gate (final-gate-1.log) passed everything at 100% coverage EXCEPT `test_a_second_migrator_waits_for_the_first_then_gives_up`: the Docker Desktop VM clock runs fast vs the host (a 10 s container sleep took 9.79 s host time), so a 1 s PostgreSQL lock_timeout returns after 0.96-0.98 s and the `>= 1` assertion fails every time. Fix the test with a tolerance (do not restart Docker while other gates run), then rerun the gate twice. Next: review Task 13 independently (not yet reviewed), then fast-forward local `main` to `feat/everything`. Nothing pushed.
  - (superseded by the line above) **Task 13 (receiver) is NOT in `main`.** Merging `feat/m3b-receiver` into `main` has 7 conflicts (.importlinter, README.md, ledger.Dockerfile, compose.yaml, runtime/assembly.py, root pyproject.toml, uv.lock) because it was written before Tasks 14-18 and signer Task 14.
    An agent (id a1c47cf8330444879) was resolving it in the worktree `C:\Users\brian\Desktop\ledger-final` (branch `feat/everything`, merge in progress, 38 uncommitted files) and was to run the gate twice. Check `git status`/`git log` there before anything; resume the agent with SendMessage or finish the merge by hand; never discard its uncommitted work.
  - **Task 19 snapshot:** local branch `wip/m3b-advance-snapshot` (commit b0da2d2) holds the unfinished, unreviewed `do_ecf.advance` work copied from the worktree `ledger-m19` (35 files; the original agent a4004426695a480c4 may still hold more). Not pushed, not in `main`.
  - A full gate on the merged tree (feat/signer == main tree) first failed because Docker Desktop was not running (`proto-lint` needs it); after starting Docker it failed again in `packages/platform` only: two timing-sensitive integration tests (`test_a_second_migrator_waits_for_the_first_then_gives_up` asserts elapsed >= 1 s and got 0.994 s; `test_a_handler_that_lost_its_lease_commits_nothing` got no claimed row under load), which also left coverage at 99.98%. Every other package passed at 100%. **First task on resume: harden those two tests (no race against wall clocks), then rerun the full gate twice.** Log: `gate-main-after-pr2-b.log` in the scratchpad.
  - Pushing again needs the owner's permission; the owner currently prefers local work only.
- **Running when this was written:**
  - **M3b Task 19** (`do_ecf.advance` job: submission, never-resend, track-id lookup, polling, parking) in worktree `ledger-m19`, branch `feat/m3b-advance`, agent id `a4004426695a480c4` (last state: gate running).
    Branch is at 40b89e8 + uncommitted/new commits: check `git log`/`git status` in `ledger-m19` before anything.
  - **M3b Task 13** (`receiver` role + `apps/receiver`) in `ledger-m13`, branch `feat/m3b-receiver` (HEAD deb866c), agent id `aca9a7682818a4e6e`: implemented, was in repeated gate runs; **not yet reviewed or merged.**
- **Remaining M3b tasks (see plan):** 13 (finish/review/merge), 19 (review + merge), 20 (fiscal: IssuePolicy, routing, recovery and mirror consumers, Renderer; binding notes from Task 12/17/18 reviews),
  21 (buyer delivery job), 22 (DO receiver routes), 23 (received documents API; commercial approval out), 24 (printed representation PDF/QR), 25 (`signer.certificate.installed` consumer),
  26 (fake DGII completed, compose, end-to-end test; needs trusted roots, `/archive` volume, PG `max_connections`), 27 (retire spike CLI, runbooks), 34 (final verification). LIVE tasks 28-31 and LATER 32-33
  are deferred; **the jury says promote Task 33 (code-4 replacement e-CF) to LIVE.**
- **Merge rule:** Tasks 17, 18, 19 go to `main` together (issue raises ChannelNotReadyError-style gaps until 19). Merging them into `feat/signer` is fine.
- Then: final whole-branch reviews, merge `feat/signer` into local `main`, run the gate on `main`. Before that merge: apply the Task 1 review item-6 text to `docs/reviews/2026-10-19-invoicing-review.md` and the plan's "Invoicing as built" (they only exist on main; expect plan-file conflicts).
- Old worktrees (`ledger-m1..m19`, `ledger-s2..s15`) still exist: delete them only when the branch is merged and the owner agrees (never delete worktrees you did not create).
- Untracked on main's working tree: `docs/reviews/2026-10-20-stack-jury-synthesis.md` and this file.

## 6. Decisions waiting for the owner (do not decide for them)

From the jury synthesis (recommendation in brackets):
1. Production hosting: AWS us-east-1 + ECS Fargate + OpenTofu [yes].
2. ERP strategy: engine now, ERP base decided by a 5-day Odoo CE vs ERPNext experiment after the first TesteCF run, no ERP modules on this chassis meanwhile [yes].
3. Signing at launch: `remote` through an INDOTEL-authorized entity (counsel to confirm) [yes; owner must get a per-signature quote].
4. Freeze list + process changes (drop mypy, tiered coverage, one review round outside fiscal/money/signer) [yes].
5. First wedge: wholesale engine for software vendors/integrators + thin portal for migrants from DGII's free tool [yes].
From the signer final review:
6. Test-mode credential can obtain documents signed with the real certificate when one certificate serves both modes: accept (document in ADR 0007) / never return signed XML to test-mode clients / test-only certificate [recommended: never return signed XML to test mode].
7. Signer does not check that the document's issuer RNC matches the certificate's company (F2, 1-2 days) [build before production].
Owner-side work (not code): file the emitter application for the owner's RNC and get TesteCF access (Owner item Q2), buy a certificate, quote remote signing, register a permanent receiver domain,
name 3 prospective customers, counsel on INDOTEL Art. 22 / Ley 172-13 vs 10-year retention / liability / separate PSFE entity. Open DGII questions are listed in the plan's Owner items (1-13) and the jury synthesis section 9.

## 7. The jury's verdict in 10 lines (full text: `docs/reviews/2026-10-20-stack-jury-synthesis.md`)

Keep Python/FastAPI/Postgres (schema per module, RLS, outbox/inbox/jobs in Postgres)/S3 object lock/isolated signer. No Redis, Kafka, Temporal. Cloud: AWS, ECS Fargate (not K8s), ALB + AWS WAF (not Cloudflare in front of the receiver),
no API gateway yet, OpenTofu cell module, Secrets Manager, Grafana Cloud, Zitadel Cloud with a custom domain. Cheap fixes now: split a test-mode archive bucket (test docs would otherwise get a 10-year compliance lock),
AWS KMS adapter, TLS on signer port, `/livez` for ALB checks, partition `audit_events`, egress proxy, supply-chain CI, check DGII token cache key (environment only; may need certificate), PITR-restore numbering runbook.
ERP: do NOT build ERP breadth on this chassis nor fork Odoo/ERPNext into it; engine + connectors first; one-way doors to decide before any ERP module: GL model (immutable entries, reversals, partitioned, async balances),
sandbox-company vs `mode` column, consolidation RLS scope, accountant cross-org model, UI architecture, master data home, third-party code policy (out-of-process only), permission naming, cell/region model.

## 8. First things to do when you resume

1. `cd C:\Users\brian\Desktop\ledger && git worktree list`; read `progress.md` tail; check the two running agents' worktrees (`ledger-m19`, `ledger-m13`) with `git status`/`git log`; resume dead agents with SendMessage.
2. Start Docker Desktop if not running. Run the full gate on `feat/signer` in `ledger-sg` (`uv sync --locked --all-packages; uv run poe check`) to re-baseline.
3. Review Task 19 with an independent opus reviewer (focus: never-resend rules, mark/track-id lookup, parking, released numbers, fencing); then Task 13 review; fix rounds; merge into `feat/signer`.
4. Continue Tasks 20-27, then 34. Keep the SDD loop. Update ledgers after every merge.
5. Ask the owner for the seven decisions above (summarise, do not re-research). Do not start platform work that depends on them (ERP modules, deploy IaC) before they answer; the CERT path is compatible with every option.
6. Do not push. Do not touch ferreteria containers. Spanish to the owner.
