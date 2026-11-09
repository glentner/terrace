# REVIEW — A handle-less `truncate` records its cut on the file an ancestor's rename has moved

> Adversarial QA by `code-review`, run in an isolated context (a fresh subagent at effort `xhigh`).
> The correctness pass grades the branch diff against [`GOAL.md`](GOAL.md) and the repo's `AGENTS.md`
> § Invariants **only**; it did not see `PLAN.md`, `TECH.md`, `research/`, anything else under `spec/`,
> or the builder's logs under `.local/`. Every finding cites an **executed** command.

- **Reviewed commit:** `9169405` (re-pinned to `e47a785`, which adds only the out-of-Scope seed note
  below)  ·  **Base:** `main` (`origin/main` at `630a406`)  ·  **Date:** 2026-11-08
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** full blind pass over `git diff origin/main...HEAD -- . ':(exclude)spec/'` (6 files,
  +398/−48). Sequential fallback: not used.

## Pre-flight

- Worktree clean, on `fix/truncate-recording-after-ancestor-rename`; `TECH.md`: `kind: fix`,
  `base: main`, `status: in_review`, `review.cycle: 0`, 3/3 phases done; `next_phase.py`: no warnings.
- **Contract drift (step 1.4), pre-confirmed by the foreman as gate judge.** `git log --oneline
  origin/main..HEAD -- spec/truncate-recording-after-ancestor-rename/GOAL.md` shows the shape commit
  `a12a249` and two more, both gate rulings: `d6d2280` records Q1–Q4 (Q4 is Geoffrey's own, given in
  the foreman's session); `3e736a5` records the plan-gate readings Q5–Q7. Graded against GOAL as of
  `9169405`.
- **Orchestrator-owned items:** R8's CI bullet (read from the saved run record) and R5's sentence "the
  plan SHALL say which, and why" (a statement in `PLAN.md`). The reviewer was told both are out of its
  scope. R7's records all sit outside `spec/` and were graded by the reviewer.
- The GOAL's Constraints cite a prior cycle's `REVIEW.md` (item 5 of Geoffrey's rulings on the cycle 3
  escalation) and cycle 4's GOAL R3/R4; the reviewer was handed that text verbatim, not the files.

## Verification run

Run by the reviewer from the worktree unless marked. Evidence is in the gitignored `.local/review1/`.

- `CI=true tools/lint.sh` → exit 0 (`gate-lint.log`). `CI=true uv run pytest -n auto -m unit`, run
  separately → **530 passed**, 0 failed, 0 errors (`gate-pytest-ci.log`).
- Baseline computed, not quoted: the same pytest command on a `git archive 630a406` extraction →
  **521 passed** (`baseline-630a406-pytest.log`). 530 > 521.
- Seeds: `CI` unset, `.hypothesis/` moved aside before and after each run; `uv run pytest
  tests/engine/test_model.py --hypothesis-seed=N` for N = 1…20 → **20/20** "1 passed", exit 0
  (`seeds.log`). `tests/engine/test_model.py` is untouched by the diff.
- Counts (`counts.log`): 540 collected, 530 `-m unit`, 192 under `tests/engine` (183 at base); these
  match `AGENTS.md`'s stated counts.
- Determinism: the 9 new tests run 30 times, 9 passed every run (`determinism.log`).
- Line length and spec ids: no added or changed line in `src/` or `tests/` exceeds 120 characters; no
  spec R#/P#/Q# id in `src/` or `tests/` (`line-length-and-ids.log`).
- Mutants, applied in place and reverted at once, tree clean after each (`mutate.py`, `run_mutants.sh`,
  `mutant-*.diff`/`.log`, `mutants-summary-1.log`, `-2.log`): MA (fix reverted) and MB (the rename's
  lock hold dropped) each turn 6 of the 9 new tests red with the seed's defects; MD (stage moves under
  the lock) turns the stage-move test red; MF/MG (the counter condition inverted or the adjustment
  dropped) turn the waiting-rename test red; MC (lock taken only after `_vacate`) and ME (guard
  restored) kill none; MP (`assert previous is not None`) passes the full 530.
- **Orchestrator:** the lock-order mutant (the rename takes the source lock *inside* its catalog
  transaction instead of around it), planted in a scratch copy of HEAD's `src/` and loaded by
  `PYTHONPATH` shadow, turns
  `test_truncate_without_a_handle_promotion_looks_up_outside_a_directory_rename_past_its_vacate` red
  with `OperationalError('database is locked')`; it passes on HEAD (`orch_m2.py`, `orch-m2.log`).
- Lock audit: `lockaudit.py` over all 192 engine tests; nothing first-acquires the source lock inside
  `db.transaction()`, and its only `locks.hold`-under-the-mutex hits are 10 calls the tests themselves
  inject on the replay's thread (`lockaudit-run.log`, `lockaudit-summary.txt`).
- Engine probes over tmpdir tiers (`test_probe_review1.py`, `probes-head.log`, and P3–P5 with
  `overlay.py` as at base in `probes-p3p4p5-baseline.log`): P1 a waiting rename at all four earlier
  landings, recorded and counted; P2 a rename past `_vacate`, `ENOENT` with nothing marked or cut; P3
  the two-tier stage-move gap, `ENOENT` after the dirty mark; P4 ENOTEMPTY out of the rename's stage
  move; P5 a three-tier promotion in the stage-move gap (F1).
- **Orchestrator, R8 CI:** `.local/ci/run.json`: run `42559854782`, event `push`, branch
  `fix/truncate-recording-after-ancestor-rename`, head `d83a748`, `completed`/`success`; jobs
  `Lint & build`, `Unit (Linux, 3.15)`, `Unit (Linux, 3.15t)`, `Unit (macOS, engine-only)` each
  `completed`/`success`, with no step concluding other than `success` or `skipped`, and `Assert libfuse
  loads` `success` on both Linux jobs. Logs: Linux 3.15 "530 passed in 52.36s", 3.15t "530 passed in
  67.64s", macOS "486 passed, 44 skipped" (all 44 under `tests/fuse`, "libfuse cannot be loaded"),
  Lint & build "All checks passed!" with sdist and wheel built; every log names `d83a748`. `git diff
  d83a748 HEAD -- . ':(exclude)spec/'` is empty and `.github/` is unchanged from `origin/main`, so the
  run covers the reviewed tree. Agrees with `TECH.md` P3's CI evidence block.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how (who) | Status |
|------|----------------|--------------------|--------|
| R1 | `rename` holds the source lock from `_vacate` through its catalog move (`85f191a`) | new tests at `test_replay.py:1925`, `:1932`, `:1939`, `:1945`, `:1951` (MA red), `:1994` (waiting rename); P1, P2 (reviewer) | ✅ |
| R2 | the same hold; recording unchanged | `assert_counted_through` after cut, flush, eviction, open/release, no negative; MA, MF, MG red; P1 (reviewer) | ✅ |
| R3 | the same hold | tests `:1939`, `:1945`, `:1963`, `:1980`; `pending() == []`; P1 (reviewer) | ✅ |
| R4 | `catalog.rename`'s one caller under the source lock | reading (`r4-r5-reading.log`) and lock audit (reviewer) | ✅ |
| R5 (code) | the `previous is not None` half removed as unreachable | ME equivalent, MP passes 530 (reviewer) | ✅ |
| R5 (plan says which, why) | `PLAN.md` "Why R5 removes the guard" and its R5 row | read (orchestrator) | ✅ |
| R6 | 9 new tests, `-m unit`, converge | 30/30 determinism; mutants as above, `:1980` by the lock-order mutant (reviewer; orchestrator for `:1980`); pre-existing tests unchanged | ✅ |
| R7 § 5/§ 10/docstrings | `AGENTS.md`, `overlay.py` | read against code and probes (reviewer) | ❌ F1 |
| R7 Known open violations | row dropped | read (reviewer) | ✅ |
| R7 root `PLAN.md` § Risks, stress seed bullet | updated | read; P4 reproduces the ENOTEMPTY claim (reviewer) | ❌ F1 |
| R7 Q3's home | dated line in `issues/freethreaded-concurrency-stress.md` | read (reviewer) | ✅ |
| R7 counts | `AGENTS.md` counts | `counts.log` (reviewer) | ✅ |
| R7 seed not named outside `spec/` | pending the roadmap step (`ROADMAP.md:35`) | grep (reviewer) | n/a until roadmap |
| R7 Q7 records | both high-blast-radius tables widened with `rename`; module docstring; replay bullet | read (reviewer) | ✅ |
| R8 lint | — | `gate-lint.log` (reviewer) | ✅ |
| R8 pytest | — | 530 > 521, 0 failed, 0 errors (reviewer) | ✅ |
| R8 seeds | — | 20/20 (reviewer) | ✅ |
| R8 CI | — | run record and logs (orchestrator) | ✅ |

Unmapped changes (possible scope creep): none.

## Findings

### F1 [HIGH/CONFIRMED] The records give one outcome for the rename's stage-move gap; the code has another

- **Where:** `AGENTS.md:422-424` (§ 5), `PLAN.md:598-600` (§ Risks),
  `issues/freethreaded-concurrency-stress.md:94-95`; the `truncate` docstring at
  `src/sosfs/engine/overlay.py:757-759` says the same, hedged with "can".
- **Failure scenario:** the records say a cut at the file's new path, after the rename releases the
  lock and before its stage copies move, "finds no stage copy to open and raises `ENOENT` after its
  dirty mark" (root `PLAN.md`: "having cut nothing"). On a three-tier stack where the file is staged on
  `scratch` and a faster `tmp` admits it, `truncate('/moved/s.txt', 2)` in that gap promotes in
  `_serve` from the logical new path and raises `TierError('No file at /moved/s.txt in tier scratch')`
  (`engine/transfer.py:91`): not an `OSError`, raised before any dirty mark, residency still
  `('clean', 5)`, no intent left open. The behaviour is the same with `overlay.py` as at base and its
  escape is a Non-goal, so the code is not the defect; the records this diff adds are, and the § 5 one
  is new § Invariants text a later cycle checks against.
- **Evidence:** `PYTHONPATH=. uv run pytest -p no:cacheprovider -v -s .local/review1/test_probe_review1.py`,
  `test_p5_stage_promotion_in_the_gap` (`probes-head.log`; base in `probes-p3p4p5-baseline.log`).
  Orchestrator re-read the four records against the log.
- **Touches:** R7 ("exactly what the code delivers"; § 5 and the docstring agree); § Invariants § 5
  text (operating-manual drift that degrades a gate).
- **Route (the property):** every record of the rename's stage-move gap names each outcome the code
  has there with the window and step that raise it, and no blanket outcome: `ENOENT` after the dirty
  mark where the new path has no stage copy to open; a non-`OSError` before any mark where `_serve`
  promotes from a slower stage; the ENOTEMPTY sequence the stress seed already states. The class is
  every record this diff adds about that gap: § 5, the `truncate` docstring (and `rename`'s, should it
  state the gap's outcomes), root `PLAN.md` § Risks and the stress seed's dated line.

### Dropped at the orchestrator's sanity check

- **"One R6 test has no mutant that turns it red"** (`test_replay.py:1980`, reported MEDIUM/CONFIRMED).
  Of the reviewer's mutants none killed it, but a mutant of the fix's own lock-order decision does:
  `OperationalError('database is locked')` (`orch-m2.log`). That meets R6 as Q6 reads it. Dropped.

### Observations (not defects)

- **MC is equivalent.** Taking the source lock only after `_vacate` survives every test and probe. A
  cut that takes the lock between `_vacate` and the move sees the file at its old path, cuts and
  records, and the move then carries the recorded file: the waiting-rename outcome R1 admits. No R-ID
  distinguishes it; not seeded.
- **ENOTEMPTY in the gap** (P4): identical at base, and recorded accurately by the stress seed's dated
  line in this diff.

### Out of Scope — recorded and homed, not a defect of this cycle

- **The `TierError` escape itself** (P5). Reproduced at `9169405` and identically at base; it lands
  before the cut's lookup under the lock, outside R1/R3's window, and the escape is the GOAL's
  Non-goal. Homed as a dated instance note in `issues/engine-error-mapping.md`, committed in
  `e47a785`; that seed's `ROADMAP.md` entry already exists.

## Human-gate triggers

- **Held unconditionally under GOAL Q4.** Geoffrey signs off on the final engine diff (`truncate`, and
  `rename`, which this diff touches) whatever the findings. That sign-off is outstanding.
- F1 meets § Invariants' high-blast-radius table through the `truncate` docstring (the `truncate` row);
  its fix is records-only and violates no `[human gate]` section.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Geoffrey's rulings on review cycle 1 (2026-11-09)

Given by Geoffrey directly in the foreman's session (2026-11-09), both as the foreman recommended:

1. **Closing route:** a records spot fix for F1, with no review cycle 2. The `truncate` docstring,
   `AGENTS.md` § 5, root `PLAN.md` § Risks and the stress seed's dated line name each outcome the code has for
   a cut in the gap between a rename releasing the source lock and moving its stage copies. Each outcome is
   scoped to the step that raises it, with no blanket outcome. Then publish.
2. **Sign-off, under GOAL Q4:** Geoffrey signed off on the engine code of `rename` and `truncate` in
   `src/sosfs/engine/overlay.py` as it stands at `9169405`. That is the rename's hold on the source lock from
   `_vacate` through its catalog move, and the counter guard without its `previous is not None` half. The
   sign-off holds only if the spot fix changes nothing but docstrings, comments and records. A change to any
   code token voids it, and the foreman returns to him.

### Spot fix (no review, per ruling 1)

- `4f12582`, `a7b493d` and `e0a6161`, built by a fresh build subagent.
  - **F1:** the `truncate` docstring, `AGENTS.md` § 5, root `PLAN.md` § Risks and the stress seed's dated line
    now name each outcome of a cut at the new path in the gap between the rename releasing the source lock and
    moving its stage copies:
    - ENOENT after the dirty mark, with nothing cut, when it serves a stage copy not yet moved;
    - `TierError`, which is not an `OSError`, out of `_serve`'s promotion before any dirty mark, when a faster
      tier admits the file;
    - a recorded cut when the file is served off the source or off a stage copy already moved.
    Each outcome was reproduced by injection at `9169405`.
- **Geoffrey's directive of 2026-11-09** (KB board, `2026/11-09-docstring-seed-cites`, signed): no docstring
  or comment in `src/` or `tests/` cites an `issues/` seed. This covers the `open` and `_serve` docstrings and
  the replay module docstring that landed in cycle 4, and this cycle's `truncate` docstring. Each now states
  its limitation in its own words.
- **Gate retuned by the foreman to match the directive** (`4f12582`, spec-only). One `records` check required
  the `truncate` docstring to name the stress seed. It now requires no `issues/` path in that docstring, and a
  new check requires `git grep 'issues/' -- src tests` to be empty at HEAD.
- **A records pin, ruled by the foreman.** Review cycle 1's seed note (`e47a785`) made
  `issues/engine-error-mapping.md` a touched seed. Its older `overlay.py` line cites then failed the gate's
  cite check. Dated pins record that those cites hold at `6a046b4`, where each names the line it describes.
- **Sign-off condition (ruling 2) holds.** The foreman parsed `src/sosfs/engine/overlay.py` and
  `tests/engine/test_replay.py` at `9169405` and at `e0a6161` with `ast`, docstrings stripped, and they are
  equal. As a control, the same comparison against `630a406` is different. The builder's own check is under
  `.local/spotfix/`.
- **Gates:**
  - no added line in `src/` or `tests/` is over 120 characters;
  - `git grep 'issues/' -- src tests` is empty;
  - P1 (`regressions mutants records suite`) is green, with **530 passed**;
  - `ledger prepush` is green.
