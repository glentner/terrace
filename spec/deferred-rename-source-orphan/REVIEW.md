# REVIEW — A commit before a queued rename leaves no orphan, and the replay converges

> Adversarial QA by `code-review`, run in an isolated context (a fresh subagent at effort `xhigh`).
> The correctness pass grades the branch diff against [`GOAL.md`](GOAL.md) + the repo's `AGENTS.md`
> § Invariants **only** — it does not see `PLAN.md`/`TECH.md` under `spec/` (avoids
> grading-its-own-homework / plan-sycophancy). Every finding cites an **executed** command, not an
> assertion.

- **Reviewed commit:** `535263c`  ·  **Base:** `main` (`origin/main` at `141aa86`)  ·  **Date:** 2026-11-06
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

## Pre-flight and contract drift

- Worktree clean, on `fix/deferred-rename-source-orphan`; `TECH.md` `status: in_review`, 5/5 phases
  done, `review.cycle` 0; `base` agrees with the profile.
- **Contract drift (confirmed by the foreman before grading).** `git log --oneline origin/main..HEAD
  -- spec/deferred-rename-source-orphan/GOAL.md` shows one commit beyond the shape commit `f670dfd`:
  `68cbfdf` "Resolve deferred-rename-source-orphan GOAL gate questions" — the GOAL-gate rulings (Q1
  splits truncate/live-stat into a follow-up seed; Q2 is the planner's choice; Q3 puts directory
  renames in scope as R2). This review grades the GOAL as of `68cbfdf`.
- **Rulings handed to the reviewer as contract-level facts** (Geoffrey, plan gate, 2026-11-06):
  `AGENTS.md` § 5 amendments D1–D3 approved in substance (`_replay_through` is the one clock-jumping
  place; a rename moves to the entry's current target; data commits, `mkdir` and ancestor placement
  set `source_path`); stale-scheduler-row (reused row id) fixes in scope; the source-namespace mutex
  held across a source-bound copy accepted with a follow-up seed; `max_examples` 1000 accepted. The
  reviewer still graded the shipped text and code against them.
- **Delegation note.** `AGENTS.md` (886 lines) was handed as a pointer to the file in the runnable
  repo rather than inline; GOAL and the rubric were inline. No R-ID is an artifact deliverable under
  `spec/` (R12 and the seeds live in the repo-root `PLAN.md`, `issues/` and `ROADMAP.md`, inside the
  graded diff), so the reviewer graded all twelve.

## Verification run

Run by the reviewer, logs in its scratch dir (`…/scratchpad/reviewer-dRSO/`); the orchestrator
re-read the logs and re-ran the F2 and F3 probes at `535263c`.

- `CI=true tools/lint.sh && CI=true uv run pytest -n auto -m unit` → lint ok; `468 passed in
  30.27s` (0 failed, 0 errors; ≥ 449).
- R10: `uv run pytest tests/engine/test_model.py --hypothesis-seed=N`, N = 1..20, no `CI`, no
  `.hypothesis/` present before each → every seed `1 passed` (24–101 s each).
- R11 (saved record, reviewer and orchestrator both read it): `run.json` for run 42294832110 —
  head `c737308`, `fix/deferred-rename-source-orphan`, `push`, `completed`/`success`; `Lint & build`,
  `Unit (Linux, 3.15)`, `Unit (Linux, 3.15t)`, `Unit (macOS, engine-only)` all `success`. Job logs
  show `468 passed` (both Linux jobs) and `424 passed, 44 skipped` (macOS engine-only, libfuse
  skips). `git diff --stat c737308 HEAD -- . ':(exclude)spec/'` is empty, so the CI tree equals the
  reviewed tree outside `spec/`.
- Engine probes at HEAD vs base (`probe.py`): R1's four routes (seed probe, `fsync` under commit
  policy, `EvictionManager`, a scheduler poll with the data deadline before the rename's) leave the
  source at `{'y.dat': b'NEWDATA!'}` after drain; base reproduces `x.dat` + `y.dat` and the remount
  adopts `x.dat`. R2 directory routes converge (base `ENOTEMPTY`). R5's EACCES and both
  stray-directory sequences converge (base EACCES / stray `d1`). R6's `mkdir` succeeds (base Errno 66).
- Mutants (reviewer's own labels, not the gate's): commit at the logical path → 6 failed incl.
  `test_model`; on `test_replay` only: no `current()` check → 4 failed; no rmdir pre-replay → 1;
  `_target` returns the logical path → 3; `_vacate` holder rows emptied → 2. A combined
  `test_model` run of the `current()` mutant passed 15 min while shrinking and was stopped (no hang
  attributable to HEAD).
- Model reach (`_commit_hop` instrumented over the CI draw, 1023 examples): 7 via `fsync`, 46 via
  rewrite-rename-commit, 0 via `commit_row`, 0 via evict (F4).
- Orchestrator re-runs at HEAD: `probe_create_size.py` → sizes `0 / 0 / flush ok / 0`, source
  `['moved', 'moved/a.txt', 'moved/c.txt']` (F2); `probe_race.py` → `mid-act queue: [(1, 'unlink')]`,
  `queue after: [] pending: 0`, `source tree: ['y.dat']`, `overlay readdir: []`, catalog
  `('/y.dat', 1, '/y.dat')` (F3). Greps for F1 and F5 below.
- Clean tree: `git status --porcelain` empty at hand-back (orchestrator re-checked); `overlay.py`
  restored from the mutant backup (sha matches); `.hypothesis/` moved aside for R10 and restored (46
  `constants` entries, no `examples`; orchestrator re-checked).

## Requirement → evidence matrix

All rows graded by the blind reviewer (no artifact R-IDs).

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `overlay.py` `_commit_hop`/`_target`/`commit_row`, `transfer.py` `copy` | `probe.py` four routes vs base; `test_replay` data-before-rename tests; mutant "commit at logical path" → 6 failed | ✅ |
| R2 | `overlay.py` `_target`/`_place_parents`, `catalog.advance_source_path` use | `probe.py` dir routes vs base; `_target` mutant → 3 failed | ✅ |
| R3 | replay family as a whole | source tree == overlay view in every probe; model invariant | ✅ |
| R4 | same | remount view == overlay view in probes; model teardown | ✅ (single-threaded; see F3) |
| R5 | `_execute_namespace_locked`, `journal.current`, rmdir pre-replay, `_holder_rows` | `r5_eacces`, `r5_stray`, `r5_stray2` vs base; mutants → 4 and 1 failed | ✅ |
| R6 | `_vacate`/`_holder_rows`/`_replay_through` | `r6` probe vs base; `_vacate` mutant → 2 failed | ✅ |
| R7 | `test_model.py` new commit-entry and poll rules, drain-by-polls convergence check | read diff; rules fire ~2000× each; M1 caught by the model | ✅ (coverage observation F4) |
| R8 | `test_model.py` | diff vs base: 11 base rules kept, strategies unchanged, `max_examples` 100→1000, `stateful_step_count` 50, no assume/precondition/filter/skip added; `fsync_policy='commit'` affects only the new fsync rule; `/seeded.txt` exempt only in the new invariant | ✅ |
| R9 | whole tree | `CI=true` verify → lint ok, `468 passed` | ✅ |
| R10 | whole tree | seeds 1–20 each `1 passed`, no `.hypothesis/` | ✅ |
| R11 | `c737308` (== HEAD outside `spec/`) | saved `run.json` + job logs; empty diff `c737308..HEAD` outside `spec/` | ✅ |
| R12 | repo-root `PLAN.md` § Risks | read: class reconciled to "open in part", names truncate/live-stat and the single-threaded mutex caveat | ⚠️ partial — does not name the threaded stale-row settle (F3) |

Constraints: the § 5 row names `issues/source-path-truncate-and-stat.md`; that seed's `file:line`
citations match HEAD and its reproduction reproduces; `ROADMAP.md` entries exist for the new seeds;
measured count 468 = 449 + 19 new; no `SCHEMA_VERSION` change; D1–D3 text matches the code.

Unmapped changes: none that are scope creep. Create-straight-onto-source placement maps to R2/D3
(and carries F2); the `_purge_source` rollback and the meta skip without `source_path` belong to the
replay family. `grep` for spec ids (R#/P#/Q#, the slug) in `src/` and `tests/` → none.

## Findings

### [HIGH/CONFIRMED] F1 — Gated replay logic moved into helpers the high-blast-radius table doesn't name
- **Where:** `src/sosfs/engine/overlay.py:1051` (`_target`), `:1092` (`_holder_rows`);
  `src/sosfs/state/journal.py:210` (`Journal.current`), `src/sosfs/state/catalog.py:140`
  (`Catalog.holder`); `AGENTS.md:221` and `:242` (the two tables).
- **Failure scenario:** At base the rename destination (`row.detail['new']`) was computed inside the
  gated `_execute_namespace`, and `_vacate`'s choice of which queued rows to run was inline. Both now
  live in `_target` (used by the rename replay, data commits, `_place_parents`, `_vacate` at
  `:1160`) and `_holder_rows` (used by `_vacate`, `_purge_source` `:535`, the rmdir pre-replay
  `:952`). The diff added `_execute_namespace_locked`, `_replay_through`, `_clear_inner_target` and
  `_place_parents` to the table but not these two, so a later cycle editing them runs no research
  and triggers no human gate. `Journal.current` (the stale-row guard) and `Catalog.holder` are named
  nowhere in `AGENTS.md`; `journal.py` is gated at file grain in the short table, but the detailed
  table names only `mark_dirty`, `begin`, `confirm`, `_resolve`.
- **Evidence:** `grep -n '_holder_rows' AGENTS.md` → no match; `_target` matches only inside
  `_clear_inner_target`; `grep -n 'def current\|def holder'` → `journal.py:210`, `catalog.py:140`
  (orchestrator re-ran). Load-bearing: `_target` → logical path mutant fails 3 `test_replay` tests;
  `_vacate` holder rows emptied fails 2.
- **Touches:** § Invariants high-blast-radius list ("a path whose logic moved gets its new home
  added"); operating-manual drift that degrades a gate.

### [MEDIUM/CONFIRMED] F2 — Create-on-source placement routes new files into the deferred live-stat defect
- **Where:** `src/sosfs/engine/overlay.py:350-365` (`create` onto the source) vs `:286` (live stat in
  `getattr`) and `:756` (`_record`).
- **Failure scenario:** One-tier stack; `rename /data → /moved` queued; `create /moved/c.txt`; write
  `b'GAMMA'`. Per D3 the bytes land at the parent's pre-rename place, but `_record` and the live stat
  stat the logical path, so the catalog size stays 0 while open, after `release`, and after a
  successful `flush` — permanently, for a file whose source object holds 5 bytes. At base the sizes
  were 5 and `flush` raised Errno 66. `test_create_on_the_source_inside_renamed_directory` passes
  because `assert_converged` compares bytes, not sizes.
- **Evidence:** `probe_create_size.py` at HEAD (orchestrator re-ran) → `size while open 0`,
  `size after release 0`, `flush ok`, `size after flush 0`, source `['moved', 'moved/a.txt',
  'moved/c.txt']`.
- **Touches:** the deferred sibling class (`AGENTS.md:633` § Known open violations row,
  `issues/source-path-truncate-and-stat.md`), whose trigger is framed as a `bypass_size` file with
  its own rename; this cycle widened the reach to any file created under an ancestor's queued rename,
  and neither the seed nor the § 5 row records that (Constraint: "deferral is recorded").

### [MEDIUM/CONFIRMED] F3 — A replayed row can settle a newer row that reused its id (threaded)
- **Where:** `src/sosfs/engine/overlay.py:945` (`journal.current(row)` check) → `:1018`
  (`journal.settle(row.id)`); `_remove` `:516-521` cancels and queues rows without the source lock.
- **Failure scenario:** The scheduler thread passes `current(row)` and performs the source rename. A
  FUSE `unlink` meanwhile (path lock only) cancels the rename row and queues an `unlink`; SQLite
  reuses the id (no `AUTOINCREMENT`). The replay's `settle(row.id)` then deletes the `unlink` row. The
  queue reports drained, the source still holds `/y.dat`, the overlay removed it, and the catalog
  keeps `('/y.dat', deleted=1, source_path='/y.dat')` with no op left — a remount would adopt
  removed data. Pre-existing (base identical), reached only by thread interleaving.
- **Evidence:** `probe_race.py` (deterministic single-thread injection of the interleaving; the
  orchestrator re-ran it at HEAD) → `mid-act queue: [(1, 'unlink')]`, `queue after: [] pending: 0`,
  `source tree: ['y.dat']`, `overlay readdir: []`, catalog `('/y.dat', 1, '/y.dat')`.
- **Touches:** R12 (the reconciled § Risks entry says stale scheduler rows are closed and "a stale row
  changes nothing", with a single-threaded caveat that points at
  `issues/freethreaded-concurrency-stress.md`, whose new R7/R8 describe a `source_path` race, not
  this queue-row race); R4 under concurrency; the in-scope stale-scheduler-row ruling.

### [LOW/CONFIRMED] F5 — `issues/audit-command.md` doesn't carry the already-wrong sizes
- **Where:** `issues/audit-command.md` (untouched by the diff).
- **Failure scenario:** GOAL Non-goals send already-wrong catalog/residency sizes "to the siblings'
  follow-up seed and to `issues/audit-command.md`". The diff names the audit only inside
  `issues/source-path-truncate-and-stat.md` (its R4); the audit seed itself says nothing about sizes.
  The binding Constraint (own seed + `ROADMAP.md` entry) is met.
- **Evidence:** `grep -ci size issues/audit-command.md` → `0`; `git diff --stat origin/main...HEAD --
  issues/audit-command.md` → empty (orchestrator re-ran).
- **Touches:** GOAL Non-goal 2 (Q5).

### Coverage observation (not a CONFIRMED defect) — F4: the model rarely reaches two data-before-rename routes
- Over the CI draw the model reached `_commit_hop` 0 times via `commit_row` and 0 via eviction (two
  random runs: 1 via `commit_row`, 0 via evict). R7's stated check holds (both ops are in the rule
  mix and fire ~2000× each; the convergence check after drain-by-polls exists and catches the
  commit-at-logical-path mutant), and `test_replay` covers both routes deterministically
  (`test_scheduler_poll_settles_data_before_rename`, `test_eviction_commit_before_queued_rename`).
  Per the rubric this is a coverage observation; its `issues/` seed and `ROADMAP.md` entry are owed
  (see Routing).

## Routing (changes-requested → `code terrace build`)

Route each as its property, the instance only as the example:

- **F1** — every function that holds deferred-op replay, commit placement or stale-row logic is
  named in both high-blast-radius tables, including `_target`, `_holder_rows`, `Journal.current` and
  `Catalog.holder`; the list only widens.
- **F2** — a file whose bytes are placed somewhere other than its logical path reports, through
  `getattr` and the catalog, the size its object holds, while open, after `release` and after drain;
  if the fix is deferred instead, the sibling seed and the § 5 row name exactly this widened trigger.
  Either way, convergence checks that compare bytes also compare sizes where the model can reach it,
  without turning on a source-served (`bypass_size`) configuration (GOAL Q7).
- **F3** — settling a replayed row settles only the op it acted on, under every interleaving of user
  ops with the scheduler, drain or flush; liveness side: replay still settles the row it ran, so the
  queue drains. Pin it with the injected interleaving in `test_replay`. If any part stays open,
  `PLAN.md` § Risks names it and a seed with an R-ID for the queue-row race owns it.
- **F5** — the audit seed records the already-wrong sizes, or the cycle states why the sibling seed
  alone is its home.
- **F4** — write the coverage-observation seed and its `ROADMAP.md` entry in the remediation build
  (this review's commit is fenced to `spec/`).

## Human-gate triggers

- **Triggered.** F3 is CONFIRMED in `_execute_namespace_locked` and `_remove`; F2 sits in
  `overlay.py` `create`'s source placement via `_place_parents`; F1 concerns the gate list itself.
  The paths are on § Invariants' high-blast-radius list (cited, not restated).
- **Standing:** Geoffrey's sign-off on the fix at review was decided at the GOAL gate (GOAL →
  Constraints) and holds until publish whatever later cycles find.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Review cycle 2 — changes-requested (2026-11-06)

- **Reviewed commit:** `548b8ea`  ·  **Base:** `main` (`origin/main` at `141aa86`)  ·  **Cycle:** 2 of ≤3
- **Mode: scoped remediation verification** (Geoffrey's choice at the review gate, 2026-11-06), not a
  fresh full pass. The fresh blind reviewer (effort `xhigh`) checked F1, F2, F3, F5 and the F4 seed
  against the properties in cycle 1's § Routing, which was handed over verbatim with a one-line
  instance per finding. It also checked whether the remediation diff (`git diff 535263c HEAD -- .
  ':(exclude)spec/'`, with the full spec-excluded diff from `origin/main` for context) introduced a
  defect or weakened anything. It re-graded R4, R7 and R12, and confirmed R9 and R11 on the new tree.
  The reviewer re-ran R10 too, because the remediation touches `getattr`, `_record` and settle code
  that the model reaches.
- **Contract drift:** none since cycle 1. `GOAL.md` is unchanged after `535263c`, and cycle 1's
  pre-confirmed `68cbfdf` still stands.
- **Rulings handed over as facts:**
  - Geoffrey's review-gate rulings (2026-11-06): fix F2 and F3 in this cycle; the
    truncate-without-a-handle case stays in `issues/source-path-truncate-and-stat.md`. The plan-gate
    rulings from cycle 1 still stand.
  - Two foreman acceptances, to be graded on whether the records describe them truthfully:
    - the threaded `rmdir`-during-replayed-`mkdir` race was seeded, not fixed;
    - `_record` now takes the source mutex for source-served copies.
- **Delegation:**
  - Handed inline: GOAL and the rubric.
  - Handed as a pointer into the runnable repo: `AGENTS.md`, as in cycle 1.
  - Blindness fence: `spec/` (except the routing text), `.local/` (except the CI record), the
    sibling worktrees, cycle 1's reviewer notes, and commit bodies.
  - No R-ID is an artifact deliverable under `spec/`.

### Verification run (cycle 2)

The reviewer ran these at `548b8ea`. Its logs and probes are in
`…/scratchpad/reviewer-dRSO-c2/`.

- `CI=true tools/lint.sh` exit 0; `CI=true uv run pytest -n auto -m unit`: **`474 passed in
  26.73s`**, 0 failed, 0 errors (≥ 449; it matches `AGENTS.md`'s measured 474).
- `CI=true` `test_model.py` → `1 passed` (24 s); `test_replay.py` → `29 passed`.
- **R10:** `--hypothesis-seed` 1–20, no `CI`, with `.hypothesis/` moved aside before every seed:
  each run `1 passed` (23.9–25.7 s).
- **R11, from the saved record:**
  - `run.json` for push run 42308408083 has head `2c4c3ac`, and all four jobs concluded `success`.
  - The job logs show `474 passed` on Linux 3.15 and on Linux 3.15t (where the GIL-disabled
    assertion step also succeeded). macOS shows `430 passed, 44 skipped`, and all 44 skips are in
    `tests/fuse/`, where libfuse is absent.
  - `git diff --stat 2c4c3ac HEAD -- . ':(exclude)spec/'` is empty.
- **Mutants of the fix code against `test_replay`.** Each file was restored afterwards and its
  sha256 verified.
  - Caught:
    - `settle_current` settling unconditionally: 6 failed.
    - The after-act settle by id: 4 failed.
    - `_settle_moot` by id: 1 failed.
    - The flush data loop by id: 1 failed.
    - `_stat_held` at the logical path: 2 failed.
    - `getattr` at the logical path: caught.
    - `_record` at the logical path: caught.
  - Survived:
    - The entry-None settle branch in `_execute_namespace_locked`. This mutant is **equivalent**,
      not a gap: a purged entry cascade-deletes its rows, so `current()` returns first.
    - `_record` without the source mutex. Only real threads can reach it, and the single-threaded
      suite has none; this matches the recorded caveat.
- **Probes:**
  - Cycle 1's `probe_create_size.py` and `probe_race.py` at HEAD now show a correct size and a
    correct settle.
  - New F2-sibling probes, all reporting the object's size while open, after release or `fsync`,
    after flush, and after a remount:
    - a `bypass_size` stage;
    - `fsync`-recorded size with the handle still open;
    - nested queued renames;
    - a handle truncate followed by a remount;
    - the file's own queued rename;
    - an evict-then-reopen off the source.
  - New F3-sibling probes: `unlink` before the act, `rmdir` during a directory-rename replay,
    `unlink` during a `meta` replay, a two-data-row scheduler snapshot, a double drain, and a
    liveness probe (`poll processed 4 queue [] pending 0`). Each one drained, and in each the source
    tree equals the catalog and the remount view equals the catalog.
- **External size watch over the model's fixed CI draw.** The engine was wrapped from outside, so
  the model source and its draw were unchanged. After every drained flush or poll, 2,777 entry-size
  and 4,086 residency-size comparisons against the tier bytes found **0 mismatches**.
- **Greps:**
  - Spec ids in `src/` and `tests/`: only `noqa: F401` (a ruff code). The slug does not appear.
  - Backticked names in both high-blast-radius tables, `origin/main` against HEAD: 25 added, none
    dropped.
- **Orchestrator re-checks at HEAD:**
  - `git status --porcelain` was empty after hand-back.
  - `probe_create_size.py` (with `PROBE_ROOT` set) printed `size while open 5`, `size after
    release 5`, `flush ok`, `size after flush 5`, and the source tree `['moved', 'moved/a.txt',
    'moved/c.txt']`.
  - `probe_race.py` printed `mid-act queue: [(1, 'unlink')]`, then `queue after: [(1, 'unlink')]`,
    then `after flush: queue [] pending 0`, `source tree: []`, and `catalog rows: [('/', 0, '/')]`.
  - `test_replay.py` → `29 passed`.
  - The F6 citations were checked with `sed -n` (below).
- **Clean tree:**
  - `git status --porcelain` empty.
  - `overlay.py` and `journal.py` restored with sha256 verified; only their mtimes changed.
  - `.hypothesis/` was moved aside before any pytest run and restored, and the 53-file hash lists
    before and after are identical.
  - No hangs and no sandbox refusals.

### Remediation status

| Finding | Status | Evidence |
|---|---|---|
| F1 | **Fixed** | Both tables name `_target`, `_holder_rows`, `Journal.current` and `Catalog.holder`, plus the new `_stat_held` and `settle_current` (`AGENTS.md:215-216`, `:223-224`, `:246-247`). The table diff against `origin/main` is 25 names added and none dropped, so the list only widened. |
| F2 | **Fixed** | `_stat_held` (`overlay.py:782-789`) backs `getattr` (`:290`) and `_record` (`:769`). The cycle 1 probe now reads 5/5/5, where it read 0/0/0. The six sibling probes give the object's size while open, after release, after drain and after a remount. `test_size_of_a_file_created_on_the_source_inside_renamed_directory` pins it, and `assert_converged` now compares `getattr` and residency sizes, remount included. The model gained no source-served configuration (`test_model.py` is unchanged), so routing's "where the model can reach it" clause has nothing to reach. The external watch above found 0 size mismatches over the CI draw. |
| F3 | **Fixed** | `settle_current` checks and deletes in one transaction (`journal.py:228-238`). Every replay settle site uses it: `_settle_moot` (`overlay.py:816-818`), the flush data loop (`:940`), and `_execute_namespace_locked` (`:975`, `:1037`). `_commit_hop` and `commit_upstream` settle by id only rows they read in the same transaction. `drain`, `_vacate`, `_replay_through`, `_purge_source` and `_clear_inner_target` all replay through `_execute_namespace`. Five injected-interleaving regressions cover the scheduler, flush, drain, `_vacate` and flush's data row, and the mutants above show that they bite. On the liveness side, a current row is still settled and the queue drains. The parts left open are named in `PLAN.md` § Risks and in `issues/freethreaded-concurrency-stress.md` R9. |
| F5 | **Fixed** | `issues/audit-command.md:189-199` records the already-wrong sizes, and its text matches observed behavior. |
| F4 seed | **Written** | `issues/model-commit-route-reach.md` exists and is linked from `ROADMAP.md:74`. The tests it cites exist (`test_replay.py:276`, `:292`). Its route table repeats cycle 1's counts (`commit_row` 0, rewrite-rename-commit 46, `fsync` 7). The cycle 2 reviewer counted differently (commits while the entry's own rename is queued: 2 / 34 / 0, eviction 0) and got the same counts at `535263c` and at HEAD. The seed's claim, that the scheduler and eviction routes are almost never reached, holds either way. |

**Foreman acceptances, as recorded:**
1. The seeded `mkdir`/`rmdir` race is described truthfully at `AGENTS.md:650` (Known open
   violations), `AGENTS.md:563-567` (§ 10), `PLAN.md:554-556` and `:581-587`, and
   `issues/freethreaded-concurrency-stress.md:74-93`, with R9 at `:141-145`.
   - Probe at HEAD: `LookupError No such intent: 1`, queue `[]`, source keeps `d`, and the catalog
     holds only `/`.
   - At `141aa86`: `LookupError No such writeback row: 1`, with the same end state. "Pre-existing"
     is accurate.
2. `_record`'s source-mutex cost is described at `AGENTS.md:552-558`, `overlay.py:94-100` and the
   `_record` docstring (`:755-757`), and that text matches the code (`:763-765`). `PLAN.md` is
   silent on the latency. That is an omission, not a false statement, and no R-ID asks for it.

### Requirement → evidence matrix (cycle 2)

All rows were graded by the blind reviewer.

| R-ID | Verified how | Status |
|---|---|---|
| R4 | `assert_converged`'s remount checks (29/29); remount view == catalog in probes g–l. Under the seeded `mkdir` race a remount adopts nothing, but the source keeps a stray `d`. That is an R3 break under threads, and it is pre-existing and recorded. | ✅ (cycle 1's qualification lifted; threaded caveat recorded) |
| R7 | Rules and the drain-by-polls convergence check unchanged (`test_model.py` 0-line diff). Thin reach is seeded (F4). | ✅ as written |
| R12 | `PLAN.md:554-589` names closed and open parts, and each claim matches a probe: truncate still raises `ENOENT`, the `mkdir` race reproduces, and the live stat is fixed. | ✅ |
| R9 | `CI=true` verify: lint exit 0, `474 passed`, 0 failed, 0 errors | ✅ |
| R11 | Run 42308408083 (`2c4c3ac`): four jobs `success`, logs read. The diff to HEAD outside `spec/` is empty. | ✅ |
| R10 | seeds 1–20 each `1 passed`, no `.hypothesis/` | ✅ (re-run) |
| R8 | `test_model.py` unchanged; `test_replay.py` only gains, apart from 2 reworded docstring lines | ✅ |
| R1–R3, R5, R6 | Full suite, `test_replay` and `CI=true` `test_model` pass. Not re-graded from scratch (scoped pass). | no regression |

Scope creep: none. Every change maps to F1–F5 or to the accepted seeding.

### Findings (cycle 2)

#### [LOW/CONFIRMED] F6 — Two seeds in the graded diff cite `file:line` locations that no longer hold the cited code
- **Where:** `issues/freethreaded-concurrency-stress.md:69` and `issues/audit-command.md` (e.g.
  `:185`).
- **What's wrong:**
  - `freethreaded-concurrency-stress.md:69` (this cycle's text) cites `overlay.py:421` for a
    directory rename's `lock(old, new)`. At HEAD, `:421` is `if
    _pure(new).is_relative_to(_pure(old)):`, and the lock is at `:425`.
  - That seed's older audit cites (`overlay.py:53`, `:650-653`, `:788-791`) match `origin/main`, not
    HEAD. `:788-791` is now `_stat_held`'s body.
  - `audit-command.md`, which the remediation edited, cites `journal.py:275`, `:268-273`,
    `overlay.py:108`, `:949`, `transfer.py:78-108`, `catalog.py:249` and `:195`. These all match
    `origin/main`, but this branch's code moves made them stale. For example, `overlay.py:949` is
    cited for "confirming before any purge"; at HEAD it is blank, and the confirm is at `:1038`.
  - The symbols are still named, so a reader can find the code.
- **Why it's in scope:** the rubric grades a seed in the diff as a claim about the repository: does
  it describe behavior at the `file:line` it cites?
- **Evidence:** the reviewer printed each cited line at HEAD and at `origin/main`. The orchestrator
  re-ran `sed -n '421p;425p' src/sosfs/engine/overlay.py` and `sed -n '949p;1038p'
  src/sosfs/engine/overlay.py`.
- **Touches:** documentation under `issues/` only. No gated behavior, and no high-blast-radius path.
- **Correct at HEAD:** `issues/source-path-truncate-and-stat.md` (`:640-641`, `:661`, `:290`,
  `:769`, `:782`, `:713`).

### Observations (not findings)

- The seed `issues/model-commit-route-reach.md:55` states that Geoffrey ruled at the review gate
  that the remediation leaves `test_model.py` alone. The orchestrator's brief did not carry that
  ruling, so this review has not confirmed it; the foreman should confirm it, or the seed misstates
  it.
- `PLAN.md` does not mention `_record`'s source-mutex latency (`AGENTS.md` does).

### Routing (changes-requested → `code terrace build`)

- **F6:** every `file:line` citation in an `issues/` seed this branch adds or edits points at the
  code it names in the tree that lands. The fix is to re-anchor each one at the final tree, or to
  pin it to a named commit that holds it. The scope is every cited location in every seed the
  branch diff touches, not only the two instances above.

### Human-gate triggers (cycle 2)

- **This cycle triggers none.** F6 is LOW, outside every high-blast-radius path, and in no
  `[human gate]` class.
- **Standing:**
  - Geoffrey signs off on the final engine diff whatever the verdict: the GOAL → Constraints gate,
    plus cycle 1's triggers (F1–F3), which hold until publish.
  - The full branch diff touches these high-blast-radius regions (per `AGENTS.md` § Invariants,
    cited, not restated):
    - `engine/overlay.py`: the commit, replay, placement and stat regions, including `getattr`,
      `_record` and `_stat_held`;
    - `state/journal.py`: `current`, `settle_current` and `settle`;
    - `state/catalog.py`: the new `holder`;
    - `engine/transfer.py`: `copy`;
    - `tests/engine/test_replay.py` and `tests/engine/test_model.py`.

## Review cycle 3 — changes-requested (2026-11-06)

> **Escalation (last allowed cycle).** Cycle 3 is the bound. This pass confirms one LOW finding,
> so the loop has not converged, and per `code-review` step 6 it stops here for Geoffrey instead of
> routing a fourth build. His call: a spot fix (one dated line), accepting the finding as recorded,
> or something else. He also owes the standing sign-off on the engine diff (below).

- **Reviewed commit:** `16082e9`  ·  **Base:** `main` (`origin/main` at `141aa86`)  ·  **Cycle:** 3 of ≤3
- **Mode: scoped remediation verification** (Geoffrey's standing choice for re-reviews this cycle).
  A fresh blind reviewer at effort `xhigh` verified F6 as its routed property, the attribution
  correction in `issues/model-commit-route-reach.md` and the new `PLAN.md` sentence on `_record`'s
  source-mutex latency. It checked the remediation diff (`git diff 548b8ea HEAD -- .
  ':(exclude)spec/'`, `PLAN.md` and five seeds, no `src/` or `tests/`) for new defects and
  weakening, and confirmed R9, R11 and R12 on the new tree.
- **Contract drift:** none since cycle 2. `GOAL.md`'s only post-shape commit is still `68cbfdf`.
- **Rulings handed over as facts:**
  - Leaving `test_model.py` unchanged was the foreman's instruction in the remediation brief.
    Geoffrey's review-gate rulings were to fix F2 now, fix F3 now, and sign off after the re-review.
  - R11 evidence is push run 42313840651 for `da8fded`, from the saved record (no `gh`).
- **Delegation:**
  - Inline: cycle 2's F6 routing text verbatim, cycle 2's two observations, and the rubric minus
    its orchestrator-only sections.
  - GOAL went inline **condensed**, not verbatim. The R-IDs, non-goals and constraints were given
    in full substance; Problem and Clarifications were summarized. None of the omitted text bears
    on a scoped pass over seeds and one `PLAN.md` sentence.
  - `AGENTS.md` went as a pointer into the runnable repo, as in cycles 1 and 2.
  - Blindness fence: `spec/`, `.local/` (except `ci-branch-42313840651/`), the sibling worktrees,
    the scratchpad outside the reviewer's own directory (the builder's cite checker and earlier
    reviewers' notes included), and commit bodies. The reviewer wrote its own cite checker.

### Verification run (cycle 3)

The reviewer ran these at `16082e9`. Its logs, checker and probe are in `…/scratchpad/reviewer-dRSO-c3/`.

- **R9:** `CI=true tools/lint.sh` exit 0 ("lint: all checks passed"); `CI=true uv run pytest -n
  auto -m unit`: **`474 passed in 26.62s`**, 0 failed, 0 errors (≥ 449). `test_model.py` (1) and
  `test_replay.py` (29) were among those collected.
- **R11, from the saved record:** `run.json` for push run 42313840651 has head `da8fded`, event
  push, conclusion success. `Lint & build` success. `Unit (Linux, 3.15)` `474 passed`. `Unit
  (Linux, 3.15t)` `474 passed`, and the free-threading assertion step ran. `Unit (macOS,
  engine-only)` `430 passed, 44 skipped`, and all 44 skips are in `tests/fuse` (libfuse absent).
  `git diff --stat da8fded HEAD -- . ':(exclude)spec/'` is empty; `16082e9` touches one file under
  `spec/`.
- **R10** was not re-run: `git diff --quiet 548b8ea HEAD -- src tests` shows no change since
  cycle 2, so the model class and its fixed draw are unchanged.
- **Cite checker** (`citecheck_c3.py`; final log `citecheck2.log`). It pairs backticks across line
  breaks, skips code fences, resolves bare `:N` to the last file named, and prints each cited
  range at HEAD, `141aa86` and `6a046b4`. Prose references ("lines N-M") were checked by hand. The
  first pass missed two wrapped cites and misattributed bare `test_concurrency.py` cites; the
  final pass fixed both. Result over the six seeds the branch adds or edits:

  | Seed | Cites | Hold at HEAD | Hold at named pin | Fail |
  |---|---|---|---|---|
  | `audit-command.md` | 43 | 31 | 12 at `141aa86` | 0 |
  | `deferred-rename-source-orphan.md` | 23 | — | 23 at `141aa86` (blanket pin, each checked) | 0 |
  | `freethreaded-concurrency-stress.md` | 45 | 34 | 10 (3 `ci.yml` at `6a046b4`, 7 `overlay.py` at `141aa86`) | **1** |
  | `model-commit-route-reach.md` | 0 | — | — | 0 |
  | `source-commit-staged-publish.md` | 3 | 3 | — | 0 |
  | `source-path-truncate-and-stat.md` | 6 | 6 | — | 0 |
  | **Total** | **120** | **74** | **45** | **1** |

- **Latency probe** (`probe_latency.py`, temporary tiers): a source commit slowed by a 1.0 s sleep
  in `put`. `mkdir`, `create`, `rename`, and `release` and `fsync` of a source-served handle each
  waited about 0.81 s. The controls did not wait: `release` of a staged handle, `getattr`,
  `readdir` and `unlink`.
- **Orchestrator re-checks at HEAD:**
  - `git status --porcelain` was empty after hand-back.
  - The F-1 cite was re-printed: `sed -n '58,64p' pyproject.toml` puts `[tool.pytest.ini_options]`
    at 58 and `markers = [` at 61, while `git show 6a046b4:pyproject.toml | sed -n '58,61p'` is
    exactly the markers list.
  - The lint and pytest summary lines and the probe table were read in the reviewer's logs.
- **Clean tree:**
  - `.hypothesis/` was moved aside before pytest and restored, and the 53-file sha256 lists before
    and after are identical. The run's own fresh `.hypothesis/` was moved to scratch, not deleted.
  - One ignored file was rewritten and not restored: `.pytest_cache/v/cache/nodeids`, pytest's
    own cache. It has no bearing on any gate. Nothing else ignored changed (`find -newer`).

### Remediation status

| Item | Status | Evidence |
|---|---|---|
| F6 | **Not met, by one cite** (F-1 below) | 119 of 120 cites hold at HEAD or at a named commit. Each dated line names its commit and states which cites it covers, sits in the same paragraph or item as those cites, and every cite it covers holds there; the reviewer graded that as meeting "pinned to a named commit that holds it". All 19 new current-line cites hold at HEAD, inside the functions they name. The three replaced lines (`freethreaded-concurrency-stress.md`'s "Line numbers are at…" sentence and `:421`→`:425`; `source-commit-staged-publish.md` `819-824`→`835-840`; the attribution sentence) are all this branch's own text. No landed seed text was removed against `origin/main`. |
| Attribution | **Corrected** | `issues/model-commit-route-reach.md:55-57` attributes leaving `test_model.py` unchanged to the foreman's remediation brief, and gives the review-gate rulings as fix F2, fix F3, sign off after the re-review. Both match the ruled facts. Its "for that reason" (the Hypothesis redraw) is a motive the rulings neither confirm nor contradict, and it matches the seed's pre-existing preceding sentence. |
| `PLAN.md` latency sentence | **Holds** | `PLAN.md:570-574`. The whole-copy hold is at `overlay.py:835-840` (`_commit_hop`). `_record` takes the mutex for a source-served copy at `:764-769`. `_vacate` takes it unconditionally at `:1178`, and is reached from `mkdir` (`:326`), `create` (`:348`) and `rename` (`:442`). `release` (`:748`) and `fsync` (`:734`) reach `_record`. It agrees with `AGENTS.md` § 10, and the probe reproduces each claim. Its pointer to `issues/source-commit-staged-publish.md` resolves. One omission, not a false claim: `fsync` under the commit policy of a staged written copy also waits, through `commit_upstream` → `_commit_hop`. The sentence doesn't claim its list is complete. |

### Requirement → evidence matrix (cycle 3)

All rows were graded by the blind reviewer.

| R-ID | Verified how | Status |
|---|---|---|
| R9 | `CI=true` verify: lint exit 0, `474 passed`, 0 failed, 0 errors | ✅ |
| R11 | Run 42313840651 (`da8fded`): four jobs `success`, logs read. Diff to HEAD outside `spec/` is empty. | ✅ |
| R12 | `grep 'resolved, Phase 8' PLAN.md` is empty. The entry reads "open in part" and names `truncate`, the removal racing a replayed `mkdir`, and the thread-level test. The remediation only added the latency sentence. | ✅ |
| R10 | Not re-run: `src/` and `tests/` are unchanged since cycle 2 | ✅ (cycle 2 evidence stands) |
| R1–R8 | Not re-graded (scoped pass). No `src/` or `tests/` change since cycle 2. | no regression |

Scope creep: none. No `status:` line changed, `ROADMAP.md` and `AGENTS.md` are untouched, and
the `ROADMAP.md` pointers resolve. Nothing was weakened.

### Findings (cycle 3)

#### [LOW/CONFIRMED] F-1 — One older cite in a touched seed drifted, and no pin covers it
- **Where:** `issues/freethreaded-concurrency-stress.md:175`. It says a new marker "needs an entry
  in `pyproject.toml:58-61`".
- **What's wrong:**
  - At HEAD, and on `origin/main` (`141aa86`), lines 58-61 are `[tool.pytest.ini_options]`,
    `addopts`, `testpaths` and `markers = [`. The markers list is at 61-64.
  - The cite was written at `6a046b4`, where 58-61 is exactly that list. The toolchain repair
    (`50a11bd`) inserted three dev-dependency lines, the same commit whose `ci.yml` drift the
    remediation pinned in this seed at `:22`. That pin covers only "the `ci.yml` line numbers
    above".
- **Context for the call:** the text predates this branch and was already stale on `origin/main`.
  It falls inside F6's routed scope ("every cited location in every seed the branch diff
  touches"), so the routed property is not met. The impact is one misdirected reader.
- **Evidence:** the reviewer ran `git show 6a046b4:pyproject.toml | sed -n 55,62p` and `sed -n
  58,64p pyproject.toml`. The orchestrator re-ran both (above).
- **Fix:** append a dated line that pins the cite at `6a046b4` and gives `pyproject.toml:61-64`.
- **Touches:** documentation under `issues/` only. No gated behavior, and no high-blast-radius path.

### Routing (changes-requested → escalated to Geoffrey, not to `code terrace build`)

- **F-1** (F6's property, unchanged): every `file:line` citation in an `issues/` seed this branch
  adds or edits points at the code it names in the tree that lands, or is pinned to a named
  commit that holds it. The one remaining instance is above. A CONFIRMED finding blocks, so the
  cycle cannot be approved as it stands. With cycle 3 the bound, the next step is Geoffrey's
  decision, not an automatic fourth build.

### Human-gate triggers (cycle 3)

- **This cycle triggers none.** F-1 is LOW, outside every high-blast-radius path, and in no
  `[human gate]` class.
- **Standing:** Geoffrey signs off on the final engine diff whatever the verdict: the GOAL →
  Constraints gate, plus cycle 1's triggers (F1–F3), which hold until publish. The full branch
  diff from `origin/main` touches these high-blast-radius regions (per `AGENTS.md` § Invariants →
  High-blast-radius paths, cited, not restated):
  - `engine/overlay.py`: the commit, replay, placement, settle and stat regions (hunks in
    `_commit_hop`, `commit_row`, `_execute_namespace`, `_execute_namespace_locked`,
    `_purge_source`, `_vacate`, `_replay_through`, `_clear_inner_target`, `_settle_moot`, `flush`,
    `_place_parents`, `_target`, `_holder_rows`, `create`, `rename`, `getattr`, `_record`,
    `_stat_held`);
  - `state/journal.py`: `current` and `settle_current`;
  - `state/catalog.py`: `holder`;
  - `engine/transfer.py`: `copy`;
  - `tests/engine/test_replay.py` and `tests/engine/test_model.py`.

## Geoffrey's rulings on the cycle 3 escalation (2026-11-06)

- **F-1 (cycle 3, LOW):** spot fix. The foreman appended one dated line under the stale cite in
  `issues/freethreaded-concurrency-stress.md`, pinning `pyproject.toml:58-61` to `6a046b4` and giving
  the current `markers` list at `pyproject.toml:61-64` (checked with `sed -n` at HEAD and at
  `6a046b4`). Publish proceeds on Geoffrey's explicit override of the cycle 3 verdict
  (changes-requested) and of the staleness gate for that one documentation line; both overrides are
  recorded in the landing confirmation.
- **Human sign-off:** Geoffrey signed off on the final engine diff (`overlay.py`, `journal.py`,
  `catalog.py`, `transfer.py`, with `test_model.py` and `test_replay.py`). That covers the GOAL →
  Constraints gate and cycle 1's F1–F3 triggers, and accepts as shipped the source-mutex latency
  (`issues/source-commit-staged-publish.md`) and the pre-existing `rmdir`-during-replayed-`mkdir`
  race recorded in `issues/freethreaded-concurrency-stress.md` R9.
