# REVIEW — `open` finds a source-served file while a replay moves it

> Adversarial QA by `code-review`, run in an isolated context (a fresh subagent at effort `xhigh`).
> The correctness pass grades the branch diff against [`GOAL.md`](GOAL.md) and the repo's `AGENTS.md`
> § Invariants **only**; it did not see `PLAN.md`, `TECH.md`, `research/`, anything else under `spec/`,
> or the builder's logs under `.local/`. Every finding cites an **executed** command.

- **Reviewed commit:** `e034985` (re-pinned to `ffa031a`, which adds only the out-of-Scope seed note
  below)  ·  **Base:** `main` (`origin/main` at `5e0d997`)  ·  **Date:** 2026-11-08
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** full blind pass over `git diff origin/main...HEAD -- . ':(exclude)spec/'` (9 files,
  +623/−57). Sequential fallback: not used.

## Pre-flight

- Worktree clean, on `fix/source-served-open-race`; `TECH.md`: `kind: fix`, `base: main`,
  `status: in_review`, `review.cycle: 0`, 3/3 phases done; `next_phase.py`: no warnings.
- **Contract drift (step 1.4), pre-confirmed by the foreman as gate judge.** `git log --oneline
  origin/main..HEAD -- spec/source-served-open-race/GOAL.md` shows the shape commit `eab7acf` and two
  more, both rulings at gates: `58151ec` records Q1–Q3 (Q3 is Geoffrey's own, via a signed board
  answer); `3acd3eb` records the plan-gate rulings Q4 and Q5 and a dated correction under Problem. No
  R-ID's SHALL text moved. Q4 grades R4 without a user rename of an ancestor during the call, which
  matches an existing Non-goal. Graded against GOAL as of `e034985`.
- **Artifact-deliverable R-IDs:** none. R6's records all sit outside `spec/` and were graded by the
  reviewer. R7's CI bullet is read from the saved run record, so the orchestrator graded it (below).
- The GOAL's `truncate` constraint cites a prior cycle's `REVIEW.md`; the reviewer was handed that
  ruling's text (item 5) verbatim, not the file.

## Verification run

Run by the reviewer from the worktree. Evidence is in the gitignored `.local/review1/`.

- `CI=true tools/lint.sh` → exit 0, "lint: all checks passed" (`verify-ci-true-lint.log`).
- `CI=true` profile verify (`tools/lint.sh && uv run pytest -n auto -m unit`) → **521 passed in
  26.73 s**, 0 failed, 0 errors; 521 > 502 (`verify-ci-true.log`).
- R7 seeds: `CI` unset, `.hypothesis/` moved aside before each run; `uv run pytest
  tests/engine/test_model.py --hypothesis-seed=N` for N = 1…20 → **20/20 exit 0**, each "1 passed"
  (23.6–26.5 s) (`seeds-01.log` … `seeds-20.log`).
- Counts (`counts.log`): 531 collected, 521 unit, 183 under `tests/engine`, 42 files; `-m integration`
  → "10 skipped, 521 deselected". These match what `AGENTS.md` states.
- Fix-removal mutants as PYTHONPATH shadow copies, no tracked edit (`make_mutants.py`, `mutants/`,
  `mutant-*.log`, each log's first line naming the module loaded). HEAD: 76 passed in the selection.
  M1 (`open` reverted to base) turns the 3 R1 tests and the source-served R4 test red; M2 (`_serve`
  reverted) turns the 2 R2 tests and the promotion R4 test red; M3/M4 (stale entry under the lock)
  likewise; M5–M8 (lock removed, or released early) turn the lock-probe tests red; M9/M10 (lookup
  without the id, or without the kind) each turn one test red. Every mutant killed. Base tree:
  15 failed, 61 passed.
- Engine probes over tmpdir tiers: `probe-r4-variants.py` at HEAD and on a `5e0d997` copy (S1–S17:
  own-rename and ancestor swaps, `create`- and `mkdir`-vacate onto the old place, after the lookup and
  at the lock wait, source-served and promoted); `probe-serve-enoent-no-intent.py`;
  `probe-seed-ancestor-roundtrip.py`; `probe-replay-vs-user-rename.py`; `probe-threads-stress.py`
  (3 real-thread runs at HEAD, about 830 opens each with a flusher, churn and eviction: 0 wrong bytes,
  0 open errors, no deadlock, converged).
- `tools/temp_site.sh` drive → `sosfs v0.1.0.dev0`, exit 0, loading the worktree's `src`
  (`temp-site-drive.log`). No CLI or mount behaviour changed; the R-IDs are engine-level.
- Line length: an `awk 'length > 120'` scan of tracked `.py` at HEAD and at base, plus `git log -L`
  on the hit (`line-length.log`).
- Greps: no spec R#/P# ids in `src/` or `tests/` (`grep-rp-ids.log`, empty); `issues/source-served-open-race.md`
  named outside `spec/` only at `ROADMAP.md:35`, the seed's own entry (`grep-seed-name.log`).
- **R7 CI, orchestrator, from the saved record** (`.local/ci/run.json`, `.local/ci/logs/*.log`): push
  run 42507324207 on `8eacba4`, conclusion `success`. `Lint & build` (128812663324) success, ruff "All
  checks passed!", sdist and wheel built; `Unit (Linux, 3.15)` (128812663347) success, "521 passed";
  `Unit (Linux, 3.15t)` (128812663266) success, "521 passed"; `Unit (macOS, engine-only)`
  (128812663102) success, "477 passed, 44 skipped", all 44 skips under `tests/fuse` naming libfuse.
  No `FAILED`/`ERROR` line in any log. `git diff --stat 8eacba4 HEAD -- . ':(exclude)spec/'` is empty
  and `.github/` at `8eacba4` equals `origin/main`'s, so CI's tree equals the reviewed tree outside
  `spec/`. Matches TECH P3's dated CI record.
- Hand-back: `git status --porcelain` empty, `git diff` empty, no build state mutated; each
  `.hypothesis/` the runs created was moved out of the worktree. Orchestrator re-checked the tree
  clean, and re-ran the line-length scan and read `probe-replay-vs-user-rename.log`.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how (by whom) | Status |
|------|----------------|------------------------|--------|
| R1 | `open` takes the source lock, `_lookup_again`, opens at `_held_path` (`overlay.py:647-650`); `0dd48f0` | 3 new `test_replay.py` tests; probes S11–S13, S16; base raises `FileNotFoundError` on the pre-rename path (reviewer) | ✅ |
| R2 | `_serve` holds the source lock from `_lookup_again` through `transfers.copy` (`overlay.py:679-683`) | 2 tests (`pending() == []`, both residencies clean); probes S14, S15, S17; `probe-serve-enoent-no-intent.log`; base raises `TierError` (reviewer) | ✅ |
| R3 | place read under the lock (`:696-699`, `:650`, `:683`, `:842`); open before release (`:650`, `:682-683`); `truncate` (`:751-777`) unchanged | read; mutants M5–M8 red; `test_open_takes_no_path_lock_and_no_lookup_under_the_source_lock`; 57 pre-existing `test_replay` tests pass (reviewer) | ✅ |
| R4 | as R1/R2, graded per Q4 | 2 tests; probes S1–S10 OK at HEAD, S1–S7 wrong bytes at base; the reused-id qualification reproduced (reviewer) | ✅ |
| R5 | `tests/engine/test_replay.py` +414/−0 | additions only (`test-replay-diff-check.log`); all 7 tests call `assert_converged`; a red mutant per sequence (reviewer) | ✅ |
| R6 | `AGENTS.md` § 5, § 10, § 11, § Known open violations, § Verification, § Conventions; docstrings; root `PLAN.md`; `ROADMAP.md`; `issues/entry-identity-token.md`; `issues/truncate-recording-after-ancestor-rename.md` | read against the code; greps; counts (reviewer). Both high-blast tables widened with `_lookup_again` | ✅ |
| R7 lint | — | `verify-ci-true-lint.log` (reviewer) | ✅ |
| R7 pytest | — | `verify-ci-true.log`, 521 passed (reviewer) | ✅ |
| R7 seeds | `tests/engine/test_model.py` untouched | `seeds-01..20.log` (reviewer) | ✅ |
| R7 CI | — | `.local/ci/run.json` and logs (orchestrator) | ✅ |

Unmapped changes (possible scope creep), none blocking: the `issues/freethreaded-concurrency-stress.md`
anchor note and its "ancestor's round trip" deferral (the GOAL Non-goals' named home; the reviewer
reproduced its claim, identical at base, `probe-seed-ancestor-roundtrip.log`); the second hunk of
`issues/truncate-recording-after-ancestor-rename.md`; a reworded sentence in that seed's `ROADMAP.md`
entry.

## Findings

### F1 [HIGH/CONFIRMED] A comment line this diff adds in `src/` exceeds 120 characters
- **Where:** `src/sosfs/engine/overlay.py:98`, the `_source_lock` comment in `OverlayEngine.__init__`,
  introduced by `0dd48f0`.
- **Failure scenario:** the line is 145 characters. § Conventions' **Code style** bullet requires
  120-character lines and grades comments in `src/`. `tools/lint.sh` runs ruff's error classes only,
  so the gate passes without checking line length.
- **Evidence:** `line-length.log`: the scan at HEAD finds this line plus three at base that the diff
  does not touch (`tier/archive.py:175`, `tests/engine/test_drain.py:53`,
  `tests/engine/test_metadata.py:112`); this diff adds the only new one. Orchestrator re-ran
  `awk 'length > 120'` on `overlay.py`: one hit, line 98, length 145; none at base.
- **Touches:** § Conventions (HIGH).
- **Route (the property):** every line this diff adds or changes in `src/` and `tests/` stays within
  120 characters, comments included. The rewrap leaves the comment's meaning unchanged.

### Out of Scope — recorded and homed, not a defect of this cycle

- **A replay racing a user directory rename can raise `LookupError` out of `flush` or `commit_row`.**
  Reproduced deterministically by injection, identically at `e034985` and `5e0d997`
  (`probe-replay-vs-user-rename.log`), and seen under real threads (`probe-threads-stress-*.log`).
  It is out of Scope: the code is untouched by this diff, the defect predates it, and it falls under
  the GOAL's Non-goals (a user rename of an ancestor; real-thread stress). Homed as a dated instance
  note under `issues/freethreaded-concurrency-stress.md` (its R7), committed in `ffa031a`; that seed's
  `ROADMAP.md` entry already exists.

## Human-gate triggers

- **Held unconditionally under GOAL Q3.** Geoffrey signs off on the final engine diff (`open`,
  `_serve` and the new source-mutex holder) whatever the findings. That sign-off is outstanding.
- F1 does not itself meet § Invariants' high-blast-radius table: the comment sits in
  `OverlayEngine.__init__`, which the table's `overlay.py` rows do not list, and it violates no
  `[human gate]` section.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Review cycle 2 — changes-requested (2026-11-08)

- **Reviewed commit:** `bab1e83` (re-pinned to `f89311b`, which adds only the out-of-Scope seed note
  below)  ·  **Base:** `main` (`origin/main` at `5e0d997`)  ·  **Date:** 2026-11-08
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3, mirrors `review.cycle` in `TECH.md`
- **Mode:** a fresh **full blind pass** over `git diff origin/main...HEAD -- . ':(exclude)spec/'` (9 files,
  +635/−57), the default for a later cycle. Not scoped to cycle 1's F1. One fresh subagent at effort `xhigh`,
  given only GOAL, the rubric, `AGENTS.md` by path, and the `truncate` ruling's text (item 5, verbatim). It was
  not given cycle 1's findings or this file. Sequential fallback: not used.

### Pre-flight

- Worktree clean, on `fix/source-served-open-race`. `TECH.md`: `kind: fix`, `base: main`,
  `status: in_review`, `review.cycle: 1`, `review.verdict: changes-requested`, 3/3 phases done.
  `next_phase.py`: no warnings.
- **Contract drift (step 1.4):** pre-confirmed by the foreman, as in cycle 1. GOAL has not changed since
  `3acd3eb`.
- **Artifact-deliverable R-IDs:** none. R7's CI bullet was graded by the orchestrator from the saved run
  record.
- Since cycle 1, the only commit outside `spec/` is `bab1e83`, which rewraps cycle 1's F1 comment.

### Verification run

The reviewer ran these from the worktree. Its evidence is in the gitignored `.local/review2/`.

- **`CI=true` profile verify:** lint exit 0, "lint: all checks passed". pytest reported **521 passed in
  26.52 s**, 0 failed, 0 errors; 521 > 502 (`verify-ci.log`). The first attempt failed before running, when the
  sandbox refused the network fetch of ruff. It was re-run with the network allowed.
- **R7 seeds:** `CI` unset and `.hypothesis/` moved aside before each run. `uv run pytest
  tests/engine/test_model.py --hypothesis-seed=N` for N = 1…20 gave **20/20**, each "1 passed"
  (23.5–25.2 s) (`seeds.log`, `seeds.sh`).
- **Counts (`counts.log`):** 531 collected, 521 unit, 183 under `tests/engine`, 76 in `test_replay.py` (+19,
  −0), and `-m integration` gave "10 skipped, 521 deselected". These match `AGENTS.md`.
- **Fix-removal mutants:** shadow packages on `PYTHONPATH`, with no tracked edit; each log names the module
  it loaded (`make_mutants.py`, `run_mutants.sh`, `mutant-*.log`, `mutant-failure-modes.log`).
  - `open_fix_removed` turns 10 `test_replay.py` tests red, including the 3 R1 tests and the source-served
    R4 test, with `FileNotFoundError` or the other file's bytes.
  - `serve_fix_removed` turns 5 red: the 2 R2 tests (`TierError`), the promotion R4 test, and two
    lock-probe tests.
  - Six lock and lookup mutants are each killed: `open`/`serve` × lookup-without-lock and lock-without-lookup,
    and `_lookup_again` without its id check or its kind check.
  - Base `overlay.py` fails with the silent baseline bytes.
- **Probes (`probes/`, `probes-head.log`, `probes-base.log`):**
  - At HEAD, R1 holds for replays by `drain`, by `commit_row` and by another path's create-vacate, and R2
    holds for replays by mkdir-vacate and by `drain`. All of these fail at base.
  - The probes also cover the out-of-Scope routes below.
- **Real-thread smoke:** about 3,300 opens in 6 s, 0 errors and no deadlock at HEAD (`stress-head.log`). The
  same smoke at base gave 24 `TierError` (`stress-base.log`).
- **Line length (`linelen.py`, `linelen.log`):** 467 added or changed lines in `src/` and `tests/`, none over
  120. The longest are 114 in `src/` and 119 in `tests/`. The orchestrator re-ran `awk 'length > 120'` on
  `overlay.py` and `test_replay.py` and found no hit. Cycle 1's F1 is fixed.
- **Greps:**
  - No spec ids in `src/` or `tests/` (`spec-ids.log`).
  - `issues/source-served-open-race.md` is named outside `spec/` only by its own `ROADMAP.md` entry, which
    Q5 allows (`r6-names.log`).
- **`tools/temp_site.sh`:** not driven this cycle. No CLI or mount behaviour changed, and every R-ID is
  engine-level.
- **R7 CI (orchestrator, from the saved record):** `.local/ci/run.json` and `.local/ci/logs/*.log`.
  - Push run 42510985498 on `bab1e83` (HEAD's code; the commits after it touch only `spec/` and the seed note
    below), branch `fix/source-served-open-race`, conclusion `success`.
  - `Lint & build` (128823779724) succeeded: ruff "All checks passed!", sdist and wheel built.
  - `Unit (Linux, 3.15)` (128823779673) succeeded with "521 passed".
  - `Unit (Linux, 3.15t)` (128823779490) succeeded with "521 passed".
  - `Unit (macOS, engine-only)` (128823779794) succeeded with "477 passed, 44 skipped". All 44 skip lines name
    libfuse, 27 in `tests/fuse/test_adapter.py` and 17 in `tests/fuse/test_mount.py`.
  - No `FAILED` or `ERROR` line appears in any log. `git diff origin/main HEAD -- .github` is empty.
  - The foreman's `open_gate.py ci` on this record came out GREEN (`.local/open-gate/f1-ci-foreman.log`). The
    previous run's record is kept in `.local/ci/prev/`.
- **Hand-back:** `git status --porcelain` empty, and no build state mutated. The only new ignored item is
  `.ruff_cache/`, from lint. Each `.hypothesis/` the runs created was moved out of the worktree. The
  orchestrator re-checked the tree and read `probes-head.log`, `probes-base.log`, `verify-ci.log`,
  `seeds.log`, `linelen.log` and the two fix-removal mutant logs.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how (by whom) | Status |
|------|----------------|------------------------|--------|
| R1 | `open`'s source branch: source lock, `_lookup_again`, `_held_path`, `open_direct` (`overlay.py:648-651`) | 3 tests; `open_fix_removed` turns them red; drain, `commit_row` and create-vacate probes (reviewer) | ✅ |
| R2 | `_serve` holds the source lock through the promotion copy (`overlay.py:680-684`) | 2 tests (`pending() == []`, residency on the faster tier); `serve_fix_removed` turns them red; mkdir-vacate and drain probes (reviewer) | ✅ |
| R3 | place read from `_lookup_again` under the lock; open or copy inside the `with`; `truncate` body unchanged | read; six lock and lookup mutants killed; existing `truncate` tests pass (reviewer) | ✅ |
| R4 | as R1 and R2, graded per Q4 | 2 swap tests; red with the other file's bytes under both fix-removal mutants and at base (reviewer) | ✅ |
| R5 | 7 sequence tests in `tests/engine/test_replay.py` (+19 tests, −0 lines) | each calls `assert_converged`, and a fix-removal mutant turns each red; 57 existing tests pass (reviewer) | ✅ |
| R6 | `AGENTS.md` § 5, § 10 (with the promotion's delay), § 11, both high-blast tables, § Known open violations, counts; root `PLAN.md`; `ROADMAP.md`; both seeds | read against code; greps; counts (reviewer). **F1:** the `open` and `_serve` docstrings say more than § 5 | ⚠️ F1 |
| R7 lint | — | `verify-ci.log` (reviewer) | ✅ |
| R7 pytest | — | `verify-ci.log`, 521 passed (reviewer) | ✅ |
| R7 seeds | `tests/engine/test_model.py` untouched | `seeds.log`, 20/20 (reviewer) | ✅ |
| R7 CI | — | `.local/ci/run.json` and logs, run 42510985498 on `bab1e83` (orchestrator) | ✅ |

None of these unmapped changes blocks:

- In `issues/freethreaded-concurrency-stress.md`: the anchor note, the deferral of the ancestor's round trip
  (the GOAL Non-goals' named home), and cycle 1's out-of-Scope note.
- The anchor rewording in `issues/truncate-recording-after-ancestor-rename.md`. The reviewer checked it at
  `6425a21`.
- The seed's `adopted:` status.
- The § 11 bullet, required by the same-commit rule.
- The supporting lock-probe and identity tests behind R3.

### Findings

#### F1 [LOW/CONFIRMED] The `open` and `_serve` docstrings promise ENOENT more widely than the code delivers

- **Where:**
  - `src/sosfs/engine/overlay.py:630-633`, the `open` docstring.
  - `src/sosfs/engine/overlay.py:666-668`, the `_serve` docstring.
  - `tests/engine/test_replay.py:44-45`, the module docstring.
- **What the text says:** "a rename or removal that took the entry off the path, or carried another entry
  onto it, raises ENOENT". Nothing limits it to a window. `AGENTS.md` § 5 scopes ENOENT to the lookup under the
  source lock (`_lookup_again: path, id and kind, raising ENOENT when it fails`). R6 requires the
  docstrings to agree with that text and to say no more than the code delivers.
- **Failure scenario:**
  - A rename of an ancestor lands after `open`'s `_ensure` and before `_serve` reads `fastest_copy`. The call
    then raises `EngineError: No resident copy of /data/big.bin` (also for `/data/s.txt`), not ENOENT.
  - An ancestor's round trip that carries a staged entry onto the path makes `open` return that entry's stage
    bytes, with no ENOENT.
- **Evidence:** `.local/review2/probes-head.log`:
  - `test_open_rename_off_the_path_right_after_the_lookup`;
  - `test_ancestor_round_trip_opens_another_staged_file`.

  Both behave identically at base (`probes-base.log`). Both behaviours are GOAL Non-goals; only the
  docstring claim is new in this diff.
- **Touches:** R6 (docstrings agree with § 5; "exactly what the code delivers and no more").
- **Severity:** LOW as the reviewer graded it. Routing is the same at any severity: a CONFIRMED finding
  blocks.
- **Route (the property):** every sentence this diff adds that says which acts the source lock covers,
  or what a call raises when the path changes under it, says exactly what the code delivers.
  - ENOENT is promised only for the lookup under the source lock, as § 5 states it.
  - The domain covers `open`'s and `_serve`'s docstrings and `test_replay.py`'s module docstring.
  - It also covers the source-lock comment in `OverlayEngine.__init__` (`overlay.py:94-97`). That comment now
    says the lock serializes "every act that reads the source where it holds a file", but `getattr`'s live
    stat reads the held place without it, as `getattr`'s docstring and § 10's holder list say. The reviewer
    showed this with `test_getattr_live_stat_reads_the_held_place_without_the_source_lock` and filed it as out
    of Scope. It is the same property in text this diff added, so it is routed with F1 and not counted
    separately.
  - Records only: no code change.

#### Out of Scope — recorded and homed, not a defect of this cycle

- **The promotion route of an ancestor's round trip is silent data loss, now reproduced.** `open` decides
  to promote a file held on the source. An ancestor is then renamed away, the file is opened and written
  there, and the ancestor is renamed back. The promotion copies the source's bytes over the dirty stage copy
  and records both residencies clean. After a flush the write is gone.
  - Evidence: `test_ancestor_round_trip_promotion_over_a_dirty_stage_copy_at_eligible`, with identical
    results at `bab1e83` and `5e0d997` (`probes-head.log`, `probes-base.log`).
  - Why out of Scope: the defect predates this diff and is a GOAL Non-goal (a user rename of an ancestor
    mid-`open`).
  - Its home, `issues/freethreaded-concurrency-stress.md`, called this case "argued, not reproduced". A dated
    note now records the reproduction, committed in `f89311b`. That seed's `ROADMAP.md` entry already
    exists.
- **The reviewer checked the seed prose this diff added to `issues/freethreaded-concurrency-stress.md` and
  found it accurate.** It reproduced both claims at HEAD and base:
  - the round trip opens another entry's stage bytes;
  - a replay racing a user rename makes `flush` raise `LookupError`, and a later flush converges.

### Human-gate triggers

- **Held unconditionally under GOAL Q3.** Geoffrey signs off on the final engine diff (`open`,
  `_serve` and the new source-mutex holder) whatever the findings. That sign-off is outstanding.
- F1 is CONFIRMED in `engine/overlay.py` docstrings of `open` and `_serve`, both listed in § Invariants'
  high-blast-radius table. So by the rubric's path test it also triggers the gate, which Q3 already holds. It
  violates no `[human gate]` section, and it changes no code.

### Convergence

- This is the second review cycle that has ended `changes-requested`, so step 6 escalates to Geoffrey.
- Both findings were records-only: cycle 1's was a line length, and this cycle's is docstring scope.
- No correctness, invariant or R-ID behaviour finding has been raised in either blind pass.

## Geoffrey's rulings on the cycle 4 escalation (2026-11-08)

Given by Geoffrey directly in the foreman's session (2026-11-08, "1(A) and 2(A), go ahead"), in answer to
the foreman's board question `2026/11-08-cycle4-review-calls`, both as the foreman recommended:

1. **Closing route (A):** a docstring-and-comment spot fix for review cycle 2's F1, with no review
   cycle 3. The `open`, `_serve` and `tests/engine/test_replay.py` module docstrings, and the
   `_source_lock` comment in `OverlayEngine.__init__`, say exactly what the code delivers: ENOENT comes
   from the lookup under the source lock only, and the lock does not cover `getattr`'s live stat. Then
   publish.
2. **Sign-off (A), GOAL Q3:** Geoffrey signed off on the engine code of `open`, `_serve` and
   `_lookup_again` in `src/sosfs/engine/overlay.py` as it stands at `bab1e83`. The sign-off holds only if
   the spot fix changes nothing but docstrings and comments. A change to any code token voids it, and
   the foreman returns to him.

### Spot fix (no review, per ruling 1)

- `7858b6e` (P1: F1 of review cycle 2), built by a fresh build subagent. Four places now say exactly what
  the code delivers:
  - the `open` and `_serve` docstrings, which now scope ENOENT to the lookup under the source lock and
    name the earlier windows (`EngineError` for no resident copy, and the ancestor round trip) as outside
    what the call guarantees, homed in `issues/freethreaded-concurrency-stress.md`;
  - `tests/engine/test_replay.py`'s module docstring, in the same terms;
  - the `_source_lock` comment in `OverlayEngine.__init__`, which lists the four acts that take the lock
    and says `getattr`'s live stat reads without it.
  No other prose on the branch made the claim.
- **Sign-off condition (ruling 2) holds.** The foreman parsed `src/sosfs/engine/overlay.py` and
  `tests/engine/test_replay.py` at `bab1e83` and at `7858b6e` with `ast`, with docstrings stripped (comments
  are not in the tree), and the two are equal. The builder's own check, under `.local/spotfix/`, agrees.
  The engine code Geoffrey signed off on is unchanged.
- Gates on `7858b6e`: no added line in `src/` or `tests/` over 120 characters; P1 (`regressions mutants
  records suite`) green, **521 passed**; `ledger prepush` green. CI on the pushed tree is recorded below.
- CI on `0e7f446` (the spot fix plus its records, pushed): run 42528181884, event `push`, all four jobs
  `success`. Linux 3.15 and 3.15t had 521 passed each with no skips. macOS had 477 passed and 44 skipped,
  every skip under `tests/fuse` and naming libfuse. The foreman graded it with `open_gate.py ci`: GREEN
  (`.local/open-gate/spotfix-ci-foreman.log`).
