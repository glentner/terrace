---
slug: nested-rmdir-replay-recursion
title: Removing nested directories after moving an entry out drains the queue instead of recursing
kind: fix
appetite: big
status: done
branch: fix/nested-rmdir-replay-recursion
base: main
current_phase: done
last_updated: '2026-11-11'
phases:
- id: P1
  name: The replay walks an explicit stack, and a row reads its needs until none is queued
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  - R9
  - R10
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/nested-rmdir-replay-recursion/verify/drain_gate.py regressions mutants records suite
- id: P2
  name: The source-only scheduler defect is recorded, and the model passes twenty seeds
  status: done
  satisfies:
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/nested-rmdir-replay-recursion/verify/drain_gate.py seedfile records suite seeds
- id: P3
  name: CI is read from the run record, and the ledger closes
  status: done
  satisfies:
  - R7
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run --no-project --python 3.15 python -I spec/nested-rmdir-replay-recursion/verify/drain_gate.py ledger records seedfile ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs
review:
  last_reviewed_commit: 6e8166cc73691be05d83ed536e8a8ddd9ad32e65
  verdict: approved
  blocked_reason: ''
  cycle: 2
---

# TECH.md — Removing nested directories after moving an entry out drains the queue instead of recursing

The **context engine and finite-state machine** for building this fix. The YAML
frontmatter above is the resume ground-truth (read it from the cycle worktree with
`uv run --with pyyaml python "$KB/bin/factory/next_phase.py" spec/nested-rmdir-replay-recursion/TECH.md`);
the per-phase checklists below are the work. `code-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run --with pyyaml python "$KB/bin/factory/set_phase.py" …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and its briefs.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done` (`done` is stamped by
  `code-publish` after confirmation, just before landing — the terminal state of the retained record)
- `appetite`: `small | big` — caps phase count and build-iteration budget (circuit breaker).
- `branch`, `base`: the cycle's branch (prefix per GOAL `kind`, from the profile's `branches`) and the
  profile's `base`.
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: GOAL R-IDs this phase delivers (traceability anchor for `code-review`).
  **Artifact-deliverable R-IDs.** Some requirements are met by a *committed document* — a research
  audit, a protocol doc, an assessment — not by CLI-observable behaviour. Attach such an R-ID to the
  phase that produces and commits the artifact, and give that phase a content `verify:`
  (`test -f …`, a `grep -q` for the sections that must exist) rather than forcing a CLI drive.
  **Say so in the phase body**, because it changes who can grade it: `code-review` blinds its reviewer
  to all of `spec/`, so an R-ID whose evidence lives there is graded by the *orchestrator*, not the
  blind reviewer. Naming it in `TECH.md` is what lets the review skill route it correctly instead of
  reporting an unverifiable requirement.
- `depends_on`: phase ids that must be `done` first (a phase is actionable only when met).
- `parallel`: `true` only for genuinely independent, non-coupled work (docs, tests, isolated leaf
  modules). Any phase touching the profile's `coupled core` is always `false`.
- `hammerable`: `false` marks a correctness/security phase that scope-hammering must **never** cut.
- `hill`: `uphill` (still figuring it out) → `crest` (unknowns resolved) → `downhill` (just
  executing). A phase stuck `uphill` across builds is a raised hand → escalate to the human.
- `attempts`: durable failed-verify counter (absent = 0), bumped by `set_phase.py --phase P<n>
  --record-attempt` on every red verify gate; `next_phase.py` warns at ≥3 — the circuit breaker
  runs on this file, not on session memory.
- `verify`: the exact command that proves the phase (prefer driving the real CLI, not just tests —
  wrap CLI drives in the profile's `throwaway environment` helper, `<helper> sh -c "…"`, so they hit
  a disposable instance, never the developer's real data). A gate carrying `$( )` or quoting the
  calling shell would mangle is set with `set_phase.py --verify-file PATH`, not `--verify`.
- `review.cycle`: completed review passes, auto-incremented by every `set_phase.py --verdict …`;
  REVIEW.md's "Cycle {n}" mirrors it and the ≤3-cycle bound is graded against it. `--verdict approved`
  also clears `review.blocked_reason` unless one is passed explicitly.

## Conventions (apply to every phase)

- Commit conventions, code style, and load-bearing invariants come from the repo's `AGENTS.md` — it
  is the constitution. Walk the sections of its § Invariants that this change touches; that is the
  curated footgun checklist.
- One phase per `code-build` invocation by default; one atomic commit containing **both** the code and
  the `TECH.md` state change. The branch commit subject is code-build's (`$KB/skill/code-build.md` →
  Step 6), not restated here; branch commits are squashed away at `code-publish` per the profile's
  `landing`. Trailers follow the profile's `branch commits`.
- The last phase carries the standing last-phase items: the deferral ledger (`code-plan` Step 6.8),
  and every item the previous cycle's last phase carried for a project-level obligation (mpsearch's
  `## Times` quote, taken as `code-build` Step 4 says). A plan that drops one says why in `PLAN.md`.
- A PR body (when the profile's `landing` opens one) ends with an attribution naming the actual
  harness, model, and variant.
- A CLI/feature change obeys the profile's `same-commit rules` (§ Conventions in `AGENTS.md`): the
  help text, docs, or generated files it names change **in the same commit**.

---

## Phase P1 — The replay walks an explicit stack, and a row reads its needs until none is queued
**Satisfies:** R1, R2, R3, R4, R5, R6, R7, R9, R10 · **Depends on:** —
**Goal:** `_replay_through` is a loop over an explicit stack, never a recursion:
- no row runs again inside its own replay;
- a row reached inside the replay of a row it needs stays queued for a later visit;
- a row reads its needs again after its prefix, until none is queued.

One `flush`, `drain` or poll, and the replay a new name triggers, then drains the GOAL's nested shapes,
five hundred chains side by side, and directory renames crossing each other's subtrees, from a fresh queue
or a restarted engine's. Every neighbouring shape drains as before. Thirteen tests (160 cases) pin it,
twelve mutants give them teeth, and the records say what the code now does, in the same commit. R7's
evidence here is committed text outside `spec/`, so the blind reviewer can grade it.

- [x] `src/sosfs/engine/overlay.py`, per PLAN § 2 "The design", items 1–5 and 8:
  - `_execute_namespace(row)` calls `_replay_through(set(), row)` under the source lock;
  - `_replay_through(rows, row=None)` is the worklist (frames, stack, `done`, the memo, the queue as read
    and extended by the rows `Journal.queued_marks` shows it has not seen, every row known by its mark);
  - `_execute_namespace_locked(row, entry)` is the act alone;
  - `_clear_inner_target(entry, queued)` returns the rows between;
  - `_holder_rows` takes `queued=` and reads holders through `Catalog.held`;
  - `_Frame` is a dataclass beside `PurgeWatch`.

  No other function's code changes. No class or module body changes but for `_Frame`. Nothing calls
  `_replay_through` or `_execute_namespace` from inside the replay.
- [x] `src/sosfs/state/catalog.py`: `Catalog.held(source_path, *, below=False)` (item 6).
  `src/sosfs/state/journal.py`: `Journal.queued_marks(to_tier=None)` after `pending` (item 7). Both are t-string
  reads, with their docstrings.
- [x] Docstrings, per PLAN § 2 "Records":
  - in `overlay.py`: `_replay_through`, `_execute_namespace`, `_execute_namespace_locked`,
    `_clear_inner_target`, `_holder_rows`, `_vacate`, `flush`;
  - `drain` in `src/sosfs/engine/drain.py`, keeping its line count so
    `issues/pump-liveness-and-loss-windows.md`'s `drain.py:98-105` keeps holding.
- [x] `tests/engine/test_replay.py`:
  - the thirteen test functions of PLAN § 6, with exactly the names and case counts in
    `verify/drain_gate.py` (`CASES`);
  - the helpers `nested_removal`, `source_tree`, `queue_of`, `sibling_chains` and `crossing`, and the
    constants `STUCK`, `NEIGHBOURS`, `CHAINS`, `CROSSINGS`, `CROSSED_NAMES`;
  - each test ends in `assert_converged`, and the R1–R4, R9 and R10 tests compare `source_tree(engine)`
    with the oracle's `tree` and call their consumer once;
  - add `time`, `PurePosixPath` and `WritebackSpec` to the imports, and the module-docstring bullets of
    PLAN § 2 "Records";
  - no existing function or class changes.
- [x] `AGENTS.md`:
  - § 5's `_replay_through` bullet;
  - `held` beside `holder` in both high-blast-radius tables;
  - `queued_marks` beside `cancel` in both journal cells of the tables;
  - the counts, each measured on the built tree: 708 unit at `:70`, `:111`, `:693`, `:734`; 718 in the
    repository map; 370 engine tests at `:136`;
  - the serial time at `:70` and `:734`, from a serial `-m unit` run on a quiet host;
  - § 10 does not change.
- [x] If a guarded line's text differs from a `MUTANTS` anchor in `verify/drain_gate.py`, re-anchor it there in
  this commit, keeping the decision it attacks and the cases it names.
- **Built (2026-11-10):** the suite times were measured on the built tree, two serial `-m unit` runs (57 s and
  59 s) and one `-n auto` run (31 s), with `test_model` alone 25 s. `AGENTS.md` now says ~60 s serial and
  ~30 s under `-n auto`: its "no faster under `-n auto`" (`:71`, `:753`) stopped holding once the 500-chain
  cases added about 25 s of serial work, so those two lines changed with the times.
- **Verify:** `drain_gate.py regressions mutants records suite`. The measured times are below.
- **Touches:** `src/sosfs/engine/overlay.py`, `src/sosfs/engine/drain.py`, `src/sosfs/state/catalog.py`,
  `src/sosfs/state/journal.py`, `tests/engine/test_replay.py`, `AGENTS.md`,
  `spec/nested-rmdir-replay-recursion/TECH.md`, and `spec/nested-rmdir-replay-recursion/verify/drain_gate.py`
  only to re-anchor.

**Reopened for review cycle 1 (amendment, 2026-11-10; `REVIEW.md` F1, F2, F4 and P1, which Geoffrey ruled a fix).**
The code was correct; the records said more than it does, one class of control was missing, and plain queues the
base drained in linear time had become quadratic. Each finding is scoped as its class:

- [x] **F1, F2: every record of when a waiting row runs names the route that runs it.** The row a `flush`, `drain`
  or poll reads starts at the bottom and never waits; a row nested in its replay may wait, and runs when the same
  call reaches it (a poll reads only the due rows); `_vacate` and `_purge_source` stop at the last row they need,
  so a row that waited past it stays queued for the next `flush`, drain or poll. Rewritten: § 5's bullet, the
  docstrings of `_replay_through`, `flush` and `_vacate`, and the test module docstring ("the replay a new name
  triggers drains"). `drain`'s docstring says only that its own rows never wait, which holds, so it keeps its text
  and its line count; `_purge_source`'s says its needed rows run first, which holds.
- [x] **F1's two sides, pinned.** `test_a_row_waiting_inside_a_new_names_replay_past_its_last_need_stays_queued_for_the_next_flush`
  (1 and 3 tiers): after `mkdir /z` returns the queue is `[rmdir /q, mkdir /z]`; one flush then drains it, matches
  the oracle and converges. Fix cases (`RecursionError` on the baseline engine). M15, a vacate that runs the whole
  queue, kills exactly these two cases; M1, M2, M4 and M10 kill them too.
- [x] **F4: R5's controls under R3's second consumer.**
  `test_shapes_beside_nested_removals_take_a_new_name_where_the_moved_entry_was`: the six neighbours × `mkdir`,
  `create` and `rename` at the moved entry's name, 18 cases; a kept entry raises `EEXIST` for `mkdir` and `create`
  and is replaced by `rename`. Pin cases (all pass on the baseline engine); M6 kills all 18, and M2, M4 and M10 the
  `one directory` and `entry removed between` ones.
- [x] **P1: a row reads the queue only when it can need one of its rows.** `_holder_rows` returns before reading
  the queue when the catalog holds no other entry in the place, and `_clear_inner_target` when the target lies
  outside the object; both take the queue as a callable that the replay evaluates (`pending()`, which adds the
  rows queued since, as the read before each row's needs did). Pinned by
  `test_a_pass_over_rows_nothing_stands_in_reads_the_queue_a_fixed_number_of_times` (flush, drain, poll): it counts
  the SQL statements that read every queued row during one call over 6 and over 60 rows (file and directory
  renames, removals, `mkdir`, `chmod`) and asserts the count does not grow. Fix cases: the baseline engine's
  `rmdir` rows each read the queue. M13 (an `rmdir` reads with no holder) and M14 (a rename reads before its
  outside check) kill them. M9's anchor moves to `pending()`'s read, the same decision. The completeness side is
  every existing case of `test_replay.py` passing unchanged, the racing-removal cases included.
- [x] **Records the change makes untrue**, in the same commit: § 5's queue-read sentence; the review's two seeds,
  `issues/replay-cost-at-scale.md` and `issues/replay-defensive-branch-coverage.md` (this cycle's own, not on
  `main`, so corrected in place, their cites into `overlay.py` pinned at `60406ae`), and the replay-cost entry in
  `ROADMAP.md`. The `records` gate was already red at `3c7f718` on those seeds' unpinned cites, before any edit here.
- [x] **The gate** (`verify/drain_gate.py`, the build's to update per PLAN § 6): the three tests join `CASES`,
  `FIX`, `PIN` and `ORACLE_TESTS`; M13–M15 join `MUTANTS` and the existing mutants name the new cases they kill;
  `records` asserts the new statements and that no file in `AGENTS.md`, `src/` or `tests/` says a waiting row
  runs "at the latest the loop" that started the replay, "where no row waits", or "runs here too".
- [x] **Counts:** 731 unit tests, 741 in the repository map, 393 engine tests.
- **Gate (2026-11-10):** green, `records regressions mutants suite`, run on this commit's tree (`ec64f660`, a throwaway
  worktree, since every mode reads committed state; only this file's frontmatter differs): 183 new cases (125 fix,
  58 pin), M1–M15 each killing its named cases, every new case named; 731 passed under `-n auto` in 33 s, lint exit
  0. A serial `-m unit` run: 731 passed in 55 s. Queue reads during one flush of N plain renames, counted: 2N + 3 at
  `3c7f718` (4,003 at N = 2,000), 3 now, 3 at the base; 2,000 renames flush in 0.87 s (10.2 s at `3c7f718`, 0.95 s at
  the base). 9,000 random sequences per engine (block's walk: mix, cross and meta profiles, 1 and 3 tiers) gave
  identical per-seed outcomes at `3c7f718` and here, the only escapes the non-goal `IntegrityError`.
- **Touches (remediation):** `src/sosfs/engine/overlay.py` (`_replay_through`, `_holder_rows`,
  `_clear_inner_target`; docstrings of `flush` and `_vacate`), `tests/engine/test_replay.py`, `AGENTS.md`,
  `issues/replay-cost-at-scale.md`, `issues/replay-defensive-branch-coverage.md`, `ROADMAP.md`,
  `spec/nested-rmdir-replay-recursion/verify/drain_gate.py`, this file.

## Phase P2 — The source-only scheduler defect is recorded, and the model passes twenty seeds
**Satisfies:** R7, R8 · **Depends on:** P1
**Goal:** the source-only scheduler `KeyError` (GOAL Q1) has its own seed,
`issues/source-only-writeback-scheduler.md`, with its reproduction and a `ROADMAP.md` entry, as a record only: `src/sosfs/engine/writeback.py` stays the base's. The profile's
`verify` is green under `CI=true` with 708 passed, equal to `AGENTS.md`'s count, and `test_model` passes for
seeds 1 to 20 from committed state.

- [x] Write `issues/source-only-writeback-scheduler.md` from `$KB/skill/_templates/ISSUE.md`: `status:
  unshaped`, `kind: fix`, the mechanism with cites into `writeback.py`, `tier/spec.py` and `fuse/mount.py`,
  the reproduction (`build_spec(root, tiers=1)`, a `mkdir`, one `WritebackScheduler(engine).poll(…)` raising
  `KeyError: 'project'`), the blame (`8d2265f`), and where it was found (this cycle, 429 of 429 mapped cases).
  It is a record, not a fix: the fix is that seed's own cycle, in `issues/source-only-writeback-scheduler.md`.
- [x] Add its `ROADMAP.md` entry under Queued, after `model-commit-route-reach`, with a `**Seed:**` line, the
  ruled order (after `rename-over-tombstoned-child` and `model-commit-route-reach` land) and one paragraph.
- [x] Run the verify. A red seed is a defect in P1's code, never a reason to touch `test_model.py`: it reopens
  P1 (`set_phase.py`), and the falsifying example becomes a deterministic regression in `test_replay.py`.
- [x] Record in the commit body: the passed count, the suite time, and the seeds' pass list.
- [x] **Amendment (GOAL B-Q1, 2026-11-10).** `AGENTS.md` § 10's re-entrancy sentence and the source lock's comment
  at `src/sosfs/engine/overlay.py:104` named replay as a reason the lock nests, which stopped holding in P1: a
  replay runs every row under its caller's hold, through `_execute_namespace_locked`. Both now give the reasons
  that still hold (`rename` around its `_vacate`, the cut around `_serve`). The comment change leaves
  `overlay.py`'s syntax tree and line count as P1 left them. `drain_gate.py records` asserts § 10 is the base's
  with only that sentence replaced, word for word, and the comment's new text in place of the old. Measured
  first: a probe wrapping the source lock over the 370 engine tests saw it taken a second time by one thread only
  as `_vacate` under `rename` (4,513 times) and `_serve` under `truncate` (4 times).
- **Verify:** `drain_gate.py seedfile records suite seeds`.

**Reopened for review cycle 1 (amendment, 2026-11-10; `REVIEW.md` F3).**
- [x] **F3: every sentence of the seed states only what the code does.** Its "stays queued until a `flush`, a
  drain or a restart runs it" is untrue: a restart starts a new scheduler whose first scan raises the same
  `KeyError`. The seed is this cycle's own file, not on `main`, so it is corrected in place; its `ROADMAP.md`
  entry makes no such claim. `seedfile` asserts the seed no longer says a restart runs the row, and says a
  restart raises again.
- **Gate (2026-11-10):** green, `seedfile records suite seeds`, on this commit's tree (`b8d9d01e`, a throwaway
  worktree; only this file's frontmatter differs): the new `seedfile` check red first on the uncorrected seed, then
  green; 731 passed under `-n auto` in 33 s; `test_model` passed seeds 1–20.
- **Touches:** `issues/source-only-writeback-scheduler.md`, `ROADMAP.md`, `spec/nested-rmdir-replay-recursion/TECH.md`;
  with the B-Q1 amendment, `AGENTS.md` (§ 10's one sentence), `src/sosfs/engine/overlay.py` (one comment line)
  and `spec/nested-rmdir-replay-recursion/verify/drain_gate.py` (the `records` check of § 10).

## Phase P3 — CI is read from the run record, and the ledger closes
**Satisfies:** R7, R8 · **Depends on:** P2
**Goal:** the four CI jobs conclude `success` on the pushed tree, read from the run record, and the records
still hold on the final tree. The evidence is a saved run record the foreman hands over, so the orchestrator
grades R8's CI bullet, not the blind reviewer.

This phase waits on CI, so it is **two runs** (code-foreman → "External waits split the run").

**Run A — check, push, stop.**
- [x] Ledger: walk P1 and P2 for deferral wording and confirm each names an `issues/` seed with a
  `ROADMAP.md` entry (`ledger` does the mechanical half; prose and list items, never headings). This plan's one
  record of deferred work is P2's, `issues/source-only-writeback-scheduler.md`.
- [x] Commit (the phase stays `in_progress`; the body says "Run A"). Run `drain_gate.py ledger prepush` on the
  committed tree. On green, stop and hand back the commit SHA for the foreman to push.

**Run B — read, grade, close.**
- [x] The foreman saves `gh run view <id> --json databaseId,headSha,headBranch,event,status,conclusion,jobs`
  for the push run to `.local/ci/run.json`, and the four job logs to `.local/ci/logs/<job id>.log`. Run the
  verify. `ci` takes the commit from `origin/fix/nested-rmdir-replay-recursion` unless `--sha=` names another;
  commits after it may touch only `spec/nested-rmdir-replay-recursion/`. Every job must conclude `success`, and
  Linux passed must equal `AGENTS.md`'s count with no skip. On macOS, passed plus skipped must equal Linux, with
  every skip under `tests/fuse` naming libfuse.
- [x] Record the run id, the pushed SHA and each job's counts in this phase, then close it.

  **Recorded (2026-11-10).** Run A's `ledger prepush` was green on `c21b808` (lint exit 0, 708 passed). The
  foreman pushed `c21b80833c2c30e3f2091f6fc2e4761cc36faeab`; CI run `42898452752` (push event, on
  `fix/nested-rmdir-replay-recursion`) concluded `success` on all four jobs, and the verify above was green
  on the saved record:

  | Job | Id | Conclusion | Counts |
  |---|---|---|---|
  | Unit (Linux, 3.15) | 130117707856 | success | 708 passed, no skip; libfuse loaded |
  | Unit (Linux, 3.15t) | 130117707987 | success | 708 passed, no skip; libfuse loaded |
  | Unit (macOS, engine-only) | 130117707435 | success | 664 passed, 44 skipped (every one under `tests/fuse`, naming libfuse) |
  | Lint & build | 130117708465 | success | — |
- **Reopened for review cycle 1 (2026-11-10).** Run A and Run B run again on the remediated tree; the record
  above is cycle 1's.

  **Run A again (2026-11-10, on the remediated tree after `4c7e7bf`).**
  - [x] Ledger: P1's and P2's remediation bodies defer nothing. The seeds they name,
    `issues/replay-cost-at-scale.md` and `issues/replay-defensive-branch-coverage.md` (the review's) and
    `issues/source-only-writeback-scheduler.md`, each have a `ROADMAP.md` entry; `ledger` is green on `4c7e7bf`.
  - [x] Commit, the phase staying `in_progress`; `drain_gate.py ledger prepush` on the committed tree, then stop for
    the foreman's push. Run B records the new CI run below the cycle-1 table.

  **Run B again (2026-11-10).** The foreman pushed `8241d4d820aac785e01b7ec527915463c22c1870`; CI run
  `42949877922` (push event, on `fix/nested-rmdir-replay-recursion`) concluded `success` on all four jobs, and the
  verify below was green on the saved record (`ledger records seedfile ci`):

  | Job | Id | Conclusion | Counts |
  |---|---|---|---|
  | Unit (Linux, 3.15) | 130291410013 | success | 731 passed, no skip; libfuse loaded |
  | Unit (Linux, 3.15t) | 130291409925 | success | 731 passed, no skip; libfuse loaded |
  | Unit (macOS, engine-only) | 130291410094 | success | 687 passed, 44 skipped (every one under `tests/fuse`, naming libfuse) |
  | Lint & build | 130291409746 | success | — |
- **Verify:** `drain_gate.py ledger records seedfile ci --run-json=.local/ci/run.json --log-dir=.local/ci/logs`.
- **Touches:** `spec/nested-rmdir-replay-recursion/TECH.md` only.

## Measured on the planning prototype (2026-11-10)

Each phase's `verify:` was dry-run on a lab, on a host shared with other sessions. The lab:
- was cloned from the cycle branch at `442dc54`, with `origin/main` at `980f998`;
- had the plan committed first, then that phase's prototype diff;
- was byte-checked against the prototype with `cmp`.

| Phase | Verify | Result |
|---|---|---|
| P1 | `regressions mutants records suite` | green in 1,223 s as one command; each mode also run separately |
| P2 | `seedfile records suite seeds` | green in 334 s; `test_model` passed seeds 1–20 on two workers, from committed state |
| P3 | `ledger records seedfile` | green |
| P3 | `ci` | dry-run only, on a synthetic run record and logs written for the lab's HEAD |

P1's modes, timed separately:

| Mode | Time | What it found |
|---|---|---|
| `regressions` | 114 s | 120 fix cases red on the baseline engine, 40 pin cases green |
| `mutants` | about 20 min | twelve mutants, run one at a time, each killing its named cases |
| `records` | 1 s | — |
| `suite` | 33 s | 708 passed, 370 engine tests, no `.hypothesis/` left |

For P3's `ci` dry run, `--sha=HEAD` was passed, since a lab's `origin/<branch>` is the shaping commit. It went
green, and red when a Linux log carried one skip. The real record is the foreman's to save at Run B.

`mutants` runs for about 20 minutes, past a 10-minute command cap. Run it, and the P1 verify that includes it,
through the background runner, as `code-build` Step 4 says.

---

## How `code-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: the cycle worktree is clean, on `branch`, and `base` is reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[{category}]` commit; stop and report.
