# REVIEW — `truncate` without a handle finds a source-served file during a queued rename

> Adversarial QA by `code-review`, run in an isolated context (a fresh subagent at effort `xhigh`).
> The correctness pass grades the branch diff against [`GOAL.md`](GOAL.md) and the repo's `AGENTS.md`
> § Invariants **only**; it did not see `PLAN.md`, `TECH.md`, `research/` or builder logs. Every
> finding cites an **executed** command.

- **Reviewed commit:** `c3f31ff`  ·  **Base:** `main` (`origin/main` at `0e6d14c`)  ·  **Date:** 2026-11-06
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** full blind pass over `git diff origin/main...HEAD -- . ':(exclude)spec/'` (9 files,
  +545/−53). Sequential fallback: not used.

## Pre-flight

- Worktree clean, on `fix/source-path-truncate-and-stat`; `TECH.md`: `kind: fix`, `base: main`,
  `status: in_review`, `review.cycle: 0`, 3/3 phases done.
- **Contract drift (step 1.4), pre-confirmed by the foreman.** `git log --oneline origin/main..HEAD --
  spec/source-path-truncate-and-stat/GOAL.md` shows the shape commit `be881e1` and one more,
  `0a88f30` ("Resolve … GOAL gate questions", GOAL.md only, +8/−5): the GOAL-gate rulings. Q1 is the
  planner's choice inside R1–R3; Q2 keeps `tests/engine/test_model.py` unchanged; Q3 hands the
  already-wrong sizes to `issues/audit-command.md`; Q4 requires Geoffrey's sign-off at review. No R-ID
  moved; graded against GOAL as of `c3f31ff`.
- **Plan-gate rulings (foreman, 2026-11-06), handed to the reviewer as facts, not as approval:** an
  `ENOENT` when another thread removes the file or renames an ancestor mid-call is accepted; the
  residual identity gap (purge, id reuse, a directory renamed onto the ancestor, inside one call's
  wait) stays recorded in `issues/source-served-open-race.md`. The reviewer graded whether the code and
  records state these truthfully (F1).
- **Artifact-deliverable R-IDs:** none. R4's owner seed (`issues/audit-command.md`) and § Known open
  violations sit outside `spec/` and were graded by the reviewer.

## Verification run

Run by the reviewer from `$WT` (probe paths under the session scratchpad,
`…/scratchpad/reviewer-sptas-c1/`, below `$R`):

- `tools/lint.sh && CI=true uv run pytest -n auto -m unit -rfEs` → lint exit 0 ("all checks passed");
  **490 passed in 27.22 s**, 0 failed, 0 errors (R5: 490 > 474).
- `CI=true uv run pytest -m unit -q` → **490 passed, 10 deselected** in 28.97 s.
- `uv run pytest --collect-only -q` → 500 total; `-m unit tests/engine` → 152; `-m integration` →
  10/500 (490 deselected). Matches every figure `AGENTS.md` now states.
- R6: `.hypothesis/` moved aside, `CI` unset; `uv run pytest tests/engine/test_model.py
  --hypothesis-seed=N -p no:cacheprovider` for N = 1…20, each from a tree with no `.hypothesis/` →
  **20/20 exit 0**, each "1 passed" (23.9–25.8 s). `test_model.py` diff is empty.
- R7, from the saved record (`.local/ci/run.json`, `.local/ci/logs/*.log`): push run 42329860978 on
  `b9b0027`, conclusion `success`; `Lint & build` success; `Unit (Linux, 3.15)` success, 490 passed;
  `Unit (Linux, 3.15t)` success, 490 passed; `Unit (macOS, engine-only)` success, 446 passed,
  44 skipped (all `tests/fuse`, libfuse absent). `git diff --stat b9b0027 HEAD -- . ':(exclude)spec/'`
  is empty, so CI's tree equals HEAD outside `spec/`.
- Mutants on scratch copies of the tree (`$R/mutate.py`), run with
  `pytest tests/engine/test_replay.py -m unit -k "truncate_without_a_handle or size_through or release_of_a_source"`:
  M1 (drop the id comparison) **16 passed** (full suite under `CI=true -n auto`: 490 passed); M2 (cut
  at the logical path) 8 failed; M3 (no source lock) 5 failed; M4 (no re-lookup) 2 failed.
- Engine-level probes over tmpdir tiers (`uv run --project $WT python …`, `PYTHONPATH=$WT/src:$WT`,
  `TMPDIR=$R/tmp`): `probe_identity_gap.py`, `probe_identity_gap_diag.py`,
  `probe_identity_gap_staged.py`, `probe_stale_serving.py` (`lock` and `upstream` hooks on the branch;
  `upstream` on a `git archive` of `0e6d14c`), `probe_stale_sizes.py`, `probe_open_race.py`.
- `tools/temp_site.sh` not used: no CLI or mount behaviour changed; every drive was engine-level.
- Orchestrator re-ran `probe_identity_gap.py` and `probe_stale_serving.py lock` at `c3f31ff`: output
  identical to the reviewer's (quoted under F1 and F2).
- Hygiene: `git status --porcelain` empty after hand-back (checked by the orchestrator).
  `.hypothesis/` (34 files) moved aside for R6 and restored; its shasum manifest after restore is
  byte-identical to the one taken before the review. A `.pytest_cache/` the runs created was moved
  aside to the reviewer's scratch, restoring the ignored set; existing `__pycache__` and
  `.ruff_cache` may hold refreshed files.

## Requirement → evidence matrix

All rows verified by the blind reviewer; none is a `spec/` artifact.

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `OverlayEngine.truncate` no-handle branch (`src/sosfs/engine/overlay.py:693-735`): re-lookup at `path` under `_source_lock`, cut at `_held_path(entry, serving)`; `_held_path` split from `_stat_held` (`overlay.py:794-804`). `6e42330`, `924d106`, `b9b0027` | `test_truncate_without_a_handle_*` (own rename shrink/grow, renamed directory, adopted, created, committed, swap, staged control) pass in the 490-passed R5 run; M2 fails 8 of them | ✅ for the GOAL's sequences; ❌ under a concurrent interleaving (F2, pre-existing) |
| R2 | No code change: `_record`/`getattr` reach `_held_path` through `_stat_held` | `test_size_through_a_bypass_stage_{behind_its_own_rename,inside_a_renamed_directory}`: handle served off the source, `getattr` = source bytes while open, `getattr` and source residency = 4 after `fsync`, `release`, `flush`; `assert_converged` | ✅ |
| R3 | Every new R1/R2 test ends `flush()` + `assert_converged` (`tests/engine/test_replay.py:178-207`): drained queue, catalog = source tree = overlay view, sizes = byte counts, fresh `OverlayEngine` agrees | R5 run | ✅ |
| R4 | `issues/audit-command.md` 2026-11-06 note; `AGENTS.md:654` § Known open violations row names it; this seed no longer named (grep) | `probe_stale_sizes.py` on HEAD re-checked every stated fact: read-only release heals the residency (1000→4), usage unchanged; `fsync` without write, `flush`, remount, rename+`flush` leave `entry.size` 1000; still served off `project`; a write or `truncate` corrects it | ✅ |
| R5 | — | `tools/lint.sh && CI=true uv run pytest -n auto -m unit` → exit 0, 490 passed | ✅ |
| R6 | `test_model.py` unchanged | 20 seeds, no `CI`, no `.hypothesis/` → 20/20 pass | ✅ |
| R7 | — | `.local/ci/run.json` + four job logs: all four jobs `success` on `b9b0027`; post-`b9b0027` diff outside `spec/` empty | ✅ |

Other checks: both high-blast-radius tables gained `_held_path` and lost nothing; no spec R/P ids in
the `src/` or `tests/` diff (grep empty); no `SCHEMA_VERSION` or `src/sosfs/state/` edit; the new
seed's two reproductions hold on HEAD (`probe_open_race.py`: `FileNotFoundError … project/data/f.bin`,
`TierError No file at /data/f.bin in tier project`); its pre-factory claim holds (`141aa86`
`overlay.py:612,633` read `source_path` unlocked).

Unmapped changes (possible scope creep): none that matters. `issues/source-served-open-race.md` and
its `ROADMAP.md` entry (a deferral § Factory requires), the `issues/model-commit-route-reach.md` note
(GOAL Q2), `test_release_of_a_source_served_copy_stats_under_the_source_lock` (pins the previous
cycle's `_record` lock), and root `PLAN.md` § Risks text (same-commit rule) map to no R-ID and are
harmless.

## Findings

### F1 [HIGH/CONFIRMED] `AGENTS.md` § 5 says a file that has taken the entry's id "is never cut"; it is cut, and the gap has no § Known open violations row
- **Where:** `AGENTS.md:401-402` (§ 5, "Reading the source resolves through it too"): "…a file that
  has since taken its id is never cut." § Known open violations (`AGENTS.md:651-665`) has no row for
  the residual gap. `issues/source-served-open-race.md:45-49` calls the gap "argued and not
  reproduced".
- **Failure scenario:** the ruling-2 chain. While a handle-less `truncate('/data/big.bin', 4)` waits
  for the source lock, another thread renames `/data→/moved`, unlinks `/moved/big.bin`, flushes,
  creates `/moved/big.bin` (500 bytes, takes id 3), flushes, renames `/moved→/data`. The re-lookup
  finds id 3 at `/data/big.bin` and cuts the new file from 500 bytes to 4.
- **Evidence:** `$R/probe_identity_gap.py` (two tiers, stage `bypass_size=100`; helper injected as the
  cut first takes the source lock, as the repo's `Interleaved` helper does), re-run by the orchestrator:
  `truncate outcome: returned (no error)` / `id at /data/big.bin now: 3 old id: 3 same: True` /
  `getattr size: 4` / `after flush source tree: {'data': 'DIR', 'data/big.bin': "b'NNNN'...len=4"}` /
  `assert_converged: FAILED`. With the new file created on the stage (`probe_identity_gap_staged.py`):
  residencies `{'project': ('clean', 4), 'scratch': ('clean', 50)}`, overlay reads 50 bytes.
- **Why HIGH:** the gap itself is accepted (plan-gate ruling 2); § 5 stating the opposite is stale
  text in § Invariants, which later plans and reviews read as a guarantee and stop checking. The seed
  also understates it: the chain reproduces by injection, and it is silent (a different file's bytes
  are cut, and convergence fails), not merely "accepted".
- **Touches:** § 5 text; operating-manual drift that degrades a gate. Region: `engine/overlay.py` —
  `truncate`, `_held_path`.

### F2 [MEDIUM/CONFIRMED] The cut re-checks the entry under the source lock, but not the tier chosen before the wait; it can cut the source while a faster copy marked clean keeps the old bytes
- **Where:** `src/sosfs/engine/overlay.py:713-735`. `serving = self._serve(entry)` (713) is decided
  before the source lock (720); inside it only path and id are re-checked (721-724), and the cut goes to
  `self.tiers[serving]`.
- **Failure scenario:** a path lock covers only its path string, so the same entry can gain a faster
  copy through a renamed ancestor between `_serve` and the lock. Helper, during the wait: rename
  `/data→/moved`; open `/moved/big.bin` O_RDWR, truncate to 0 through the handle, write 10 bytes,
  release; open read-only (promotes to `scratch`), release; rename `/moved→/data`. No purge, no id
  reuse: neither plan-gate ruling covers it. The cut lands on the source; `getattr` reports 4, a fresh
  handle reads the 10 stage bytes, `flush()` does not reconcile, and evicting the clean stage copy later
  changes what `read` returns with no write in between.
- **Evidence:** `$R/probe_stale_serving.py lock`, re-run by the orchestrator: `truncate returned` /
  `getattr size: 4` / `residencies: {'project': ('clean', 4), 'scratch': ('clean', 10)}` / `read via
  overlay: b'0123456789'` / `after flush read: b'0123456789'` / `evict stage copy: True` / `after
  evict read: b'0123'`. The `upstream` hook (after `_serve`) gives identical output on the branch and on
  `0e6d14c`: **pre-existing**; the diff adds a blocking wait inside the window and a re-check that reads
  as covering concurrent moves (§ 5 text, the `truncate` docstring) but does not cover this one.
- **Not recorded anywhere:** the seed's related-window paragraph says writes keyed by an old path
  "match nothing"; its residual-gap paragraph covers only id reuse.
- **Severity note for Geoffrey:** graded MEDIUM (edge path: three concurrent ops inside one call). A
  strict reading could grade it CRITICAL: a residency marked `clean` whose bytes differ from the source
  is the untruthful `state` § 7 rests on, and by argument (not run) a later write through the stale
  stage copy would commit upstream and silently undo the truncate. Your call at the gate.
- **Touches:** R1's read clause under its WHEN-condition; § 7's premise. Region: `engine/overlay.py` —
  `truncate`, `_serve`, `_held_path`.

### F3 [LOW/CONFIRMED] No test exercises the id comparison; the test named for it passes on the path check alone
- **Where:** `src/sosfs/engine/overlay.py:722` (`or current.id != entry.id`);
  `tests/engine/test_replay.py:881`
  (`test_truncate_without_a_handle_leaves_a_file_that_reused_the_entry_id_alone`).
- **Failure scenario:** with the id comparison removed, a different file carried onto the path during
  the wait would be cut, and the suite stays green. The named test raises because the new file sits at
  `/moved/big.bin`, not at `/data/big.bin`, so the path check alone satisfies it; its comment ("the id
  was reused, or the test proves nothing") implies otherwise.
- **Evidence:** mutant M1 (`$R/mut/M1-no-id-check/`): the 16 targeted tests `16 passed, 29
  deselected`; full suite `CI=true -n auto` `490 passed in 26.90s` (`$R/mut/M1-full.log`). M2–M4 are
  killed (8, 5, 2 failures).
- **Touches:** coverage of a reachable behaviour of the R1 code path. Region: `engine/overlay.py` —
  `truncate`; `tests/engine/test_replay.py`.

Dropped by the reviewer after checking: lock order (path lock → source lock → `db.transaction()`)
matches § 10; `mark_dirty` still precedes the cut on the staged path (§ 1); `catalog.get` hides
tombstones; the non-source branch adds no new wait. PLAUSIBLE findings: none.

### Routing to build (properties, not mechanisms)

- **F1 — records state the identity check's guarantee exactly.** Every record of the handle-less cut
  (§ 5, the `truncate` docstring, test docstrings and comments, `issues/source-served-open-race.md`)
  claims no more than the code delivers and leaves nothing it admits unrecorded: what the check
  rejects, that a file which has taken the id and been carried onto the path *is* cut, that the chain
  reproduces by injection and is silent; and § Known open violations carries a row for it naming its
  seed. Both sides: no guarantee stronger than the code, no admitted case missing from the record.
- **F2 — a handle-less cut acts on the copy that serves the file when the cut is made, and leaves no
  residency marked clean whose bytes differ from the source.** Every decision taken before the
  source-lock wait (serving tier, upstream, the `mark_dirty` target) holds for the entry as it stands
  under the lock, or the call does not cut. Completeness side, pinned alongside: the GOAL's R1 sequences,
  uncontended, still never raise and still converge. If Geoffrey instead rules the pre-existing window
  out of this cycle, that is his call at the human gate, and it needs a seed plus a § Known open
  violations row (the orchestrator does not write `issues/`).
- **F3 — mutant sweep** over every decision in `truncate`'s no-handle branch and `_held_path`, each
  mutant run under `-m unit` (and `CI=true`), equivalent mutants recorded with the reason; the id
  comparison gets a test in which a different id reaches the path the call holds.
- F2 and F3 reopen `truncate`'s no-handle branch, which done phases built; `code-build` retunes the
  gate of any phase the fix reopens (Step 1.3).

## Human-gate triggers

**Triggered.** Geoffrey's sign-off on the engine diff is required whatever the verdict (GOAL Q4), and
every CONFIRMED finding here sits in regions on `AGENTS.md` § Invariants → *High-blast-radius paths
(human gate)* (that table is the only copy):

- F1 (HIGH): the `engine/overlay.py` — `write`, `_mark_handle_dirty`, `truncate` row, and the `open`,
  `_serve`, …, `_held_path` row (§ 5 text describing them).
- F2 (MEDIUM): the same two rows (`truncate`, `_serve`, `_held_path`).
- F3 (LOW): the `truncate` row and the `tests/engine/test_replay.py`, `tests/engine/test_model.py` row.

High-blast-radius regions the diff touches: `engine/overlay.py` `truncate` (rewritten no-handle
branch), `_stat_held` and the new `_held_path`; `tests/engine/test_replay.py` (+361 lines); both
high-blast-radius tables in `AGENTS.md` (widened by `_held_path`; nothing removed).

## Optional completeness sub-pass

Not run (not requested).

## Geoffrey's rulings at the review gate (2026-11-06)

- **F2:** fix in this cycle (not deferred). Severity recorded as **MEDIUM**: an edge-path race that
  needs a narrow concurrent interleaving and predates the cycle.
- **F1, F3:** routed to build as written.
- **Sign-off:** after the re-review, on the final engine diff, as in the previous cycle.

## Review cycle 2 — changes-requested (2026-11-07)

- **Reviewed commit:** `ea38083`  ·  **Base:** `main` (`origin/main` at `0e6d14c`)  ·  **Cycle:** 2 of ≤3
- **Mode:** **scoped to verifying the remediation** of F1–F3 (Geoffrey's standing choice for
  re-reviews): `git diff c3f31ff HEAD -- . ':(exclude)spec/'` (5 files, +136/−40), with the full
  spec-excluded diff from `origin/main` for context. The reviewer also hunted F2's whole class (any value
  decided before the source lock and used after it) and checked that R1, R5, R6 and R7 hold on the new
  tree. Fresh blind subagent at effort `xhigh`; sequential fallback not used.
- **Contract drift:** none since cycle 1 (`git log origin/main..HEAD -- spec/…/GOAL.md` shows `be881e1`
  and `0a88f30` only). Rulings handed over as facts: the plan-gate rulings (ENOENT on a concurrent
  removal or ancestor rename accepted; the identity gap accepted and recorded) and the review-gate
  rulings (F2 fixed this cycle, MEDIUM).
- **Inputs to the reviewer:** GOAL.md, the rubric, `AGENTS.md`, the two diffs and the one-line log; the
  cycle 1 plan-gate rulings, F1–F3, § Routing and the review-gate rulings; the cycle 1 probe scripts
  `probe_stale_serving.py` and `probe_identity_gap.py` only. Blindness slip, reported by the reviewer:
  while tracing `source_path` history it saw a fragment of the body of `8dcc0d0` (2026-07-12, before
  this branch), which said nothing about this cycle.

### Verification run

Probes under `…/scratchpad/reviewer-sptas-c2/probes/` (`$RV`), run as
`PYTHONPATH=$WT/src:$WT uv run --project $WT python probes/<probe> <arg>` with cwd `$RV`; comparison
trees for `c3f31ff` and `0e6d14c` under `$RV/trees/`.

- R5: `tools/lint.sh && CI=true uv run pytest -n auto -m unit -rfEs` → lint "all checks passed";
  **493 passed in 28.15 s**, 0 failed, 0 errors (> 474). `--collect-only`: 503 total, 493 unit,
  10 integration, 155 engine unit; the counts `AGENTS.md` states match.
- R6: `.hypothesis/` moved aside (35-file manifest), `CI` unset; `uv run pytest
  tests/engine/test_model.py --hypothesis-seed=N -p no:cacheprovider`, N = 1…20, each from a tree with
  no `.hypothesis/` → **20/20 "1 passed"**. `test_model.py` unchanged.
- R7, from `.local/ci/run.json` and `.local/ci/logs/`: push run 42342479853 on `7eba2ad`, conclusion
  `success`. `Lint & build` success ("All checks passed!", build ok); `Unit (Linux, 3.15)` 493 passed;
  `Unit (Linux, 3.15t)` 493 passed; `Unit (macOS, engine-only)` 449 passed, 44 skipped (all
  `tests/fuse`, "Unable to find libfuse"). `git diff --stat 7eba2ad HEAD -- . ':(exclude)spec/'` is
  empty: `ea38083` touches only `spec/`, so CI's tree is HEAD outside `spec/`.
- R1: `CI=true uv run pytest -m unit tests/engine/test_replay.py -k "truncate_without_a_handle or
  size_through_a_bypass"` → 18 passed; every R1 sequence ends in `assert_converged`.
- Mutant sweep (22 mutants over `truncate`'s no-handle branch and `_held_path`, one `git archive HEAD`
  tree each): targeted 103 tests, survivors run on the full `-n auto -m unit` suite. Table under F3.
- Grep: no spec R/P ids in `src/` or `tests/`; no `src/sosfs/state/` or `SCHEMA_VERSION` edit.
- **Orchestrator re-ran** at `ea38083` `probe_stale_serving_c2.py serve`, `probe_flip_to_source.py
  wrongobj` and `lockprobe`, `probe_idgap_symlink.py`, `probe_idgap_kind.py dir` and
  `probe_unlocked_rename.py`. Output was identical to the reviewer's (quoted below).
- `tools/temp_site.sh` was not used: every drive was engine-level.
- Hygiene: `git status --porcelain` empty after hand-back (checked by the orchestrator), HEAD still
  `ea38083`. `.hypothesis/` restored byte-identically (manifest `cmp` identical). The `.pytest_cache/`
  and per-run `.hypothesis/` that the runs created were moved to `$RV/moved-aside/`. Worktree
  `__pycache__`, possibly `.ruff_cache`, and the scratch uv cache changed.

### Remediation status

| Cycle 1 finding | Status at `ea38083` |
|---|---|
| F1 (HIGH) records state the identity check exactly | **Partial; still open.** The file case is now stated truthfully everywhere (§ 5, the new § Known open violations row, the seed, PLAN § Risks, the docstring, the tests). `probe_identity_gap.py` at HEAD: `returned (no error)` / `same: True` / `getattr size: 4` / `assert_converged: passed`. The remediation adds new overclaims, and a non-file variant is unrecorded (F4, F5). |
| F2 (MEDIUM) the cut acts on the copy that serves at cut time | **Instance fixed; property not met across its domain.** `probe_stale_serving.py lock` at HEAD: `residencies : {'project': ('clean', 10), 'scratch': ('dirty', 4)}`, `read via overlay : b'0123'`, `after evict read : b'0123' getattr: 4` (at `c3f31ff`: `('clean', 4)` / `('clean', 10)`, reading `b'0123456789'`). The `upstream` hook now fires inside the source lock and deadlocks its helper, so the reviewer moved it in a copy to just after `_serve` (`probe_stale_serving_c2.py serve`); the output is the same and correct. The completeness side holds: R1's sequences, run uncontended, pass and converge. But the lock decision itself is still taken before the wait and never re-made (F4), and so is the kind check (F5). |
| F3 (LOW) id comparison tested; mutant sweep | **Partial; still open.** M08 (drop `current.id != entry.id`) is killed by `test_truncate_without_a_handle_leaves_another_file_carried_onto_the_path_alone`, which meets the routed test. The sweep has two real survivors (M20, M03), and E1 is not strictly equivalent. |

### Findings (cycle 2)

#### F4 [CRITICAL/CONFIRMED] Introduced by the remediation: the cut can land on the source without the source lock, and silently truncate another file
- **Where:** `src/sosfs/engine/overlay.py:719-720` (`on_source` comes from `_serve` and picks the lock),
  `:725` (the serving tier is re-decided), `:730` (the cut).
- **Failure scenario:** `_serve` chooses a stage copy, so `nullcontext` is taken and the source lock is
  not held. Before the re-decision, another thread makes a round trip through an ancestor, which the
  path lock does not cover: `rename /data→/d2`, `evict_copy` of the stage copy, `rename /d2→/data`.
  `fastest_copy` now returns the source. The cut resolves `source_path` and truncates the source object
  with the source lock free, which § 10's "`truncate` without a handle takes it to cut one" forbids. A
  replay can then move or replace the object under the cut.
- **Evidence:**
  - `probe_flip_to_source.py lockprobe` printed `cut: source.open_direct /data/s.txt -- another thread
    could take the source lock: True`.
  - `wrongobj` variant: in the unlocked window, another thread moves a different file onto the old
    source place (`mkdir /other`, write `OTHER`, flush, `rename /other→/data`, whose `_vacate` replays
    `/data→/d2`, flush). The probe printed:
    - `truncate returned`
    - `source tree : {'d2/s.txt': b'small', 'data/s.txt': b'OT'}`
    - `getattr size : 2` / `read via overlay : b'small'`
    - `other file /data/s.txt: catalog size 5 source bytes b'OT'` / `assert_converged: FAILED`
  - The reviewer's `flush` variant raises `FileNotFoundError … project/data/s.txt` for a file that
    exists. On the same interleaving, `c3f31ff` raises `LookupError` and cuts nothing.
  - Mutant M05 (always take the source lock) passes the suite and blocks the helper.
- **Severity:** CRITICAL by the rubric's letter: it violates § 10 and silently corrupts another file's
  bytes. In practice the window is narrow (no blocking call inside it, narrower than F2's), and it
  needs an injected interleaving. Geoffrey's call at the gate, as with F2.
- **Touches:** § 10, § 5; the `truncate` row and the `…, _held_path` row of the high-blast-radius table.

#### F1 (reopened) [HIGH/CONFIRMED] The records claim the cut re-decides everything under the lock; F4 and F5 show it does not
- **Where:**
  - `AGENTS.md:401-402` (§ 5): "The cut decides again under that lock everything it decided before the
    wait."
  - `AGENTS.md:562` (§ 10): "`truncate` without a handle takes it to cut one". This is false in F4's
    flip.
  - The `truncate` docstring (`overlay.py:697`): "Everything decided before that lock is decided again
    inside it."
  - `issues/source-served-open-race.md:34-37`: "closes it there".
  - Root `PLAN.md:580`: "the copy that serves it found again there".
  - The § Known open violations row (`AGENTS.md:660`) and the seed's residual-gap paragraph say the call
    "cuts the new file". They leave out the non-file variant (F5).
- **Evidence:** the F4 and F5 probes above and below.
- **Why HIGH:** stale text in § Invariants that later plans and reviews will read as a guarantee.

#### F5 [MEDIUM/CONFIRMED] The kind is checked only before the wait: in the identity-gap chain a symlink or directory that took the id is acted on
- **Where:** `src/sosfs/engine/overlay.py:716` (the kind check, before the wait), `:721-725` (the
  re-lookup checks path and id, not kind).
- **Failure scenario:** the accepted identity-gap chain, but a symlink takes the purged id: it is
  adopted from a directory nobody has listed, renamed onto `/moved/big.bin`, and `/moved` is renamed to
  `/data`. `open_direct` has no `O_NOFOLLOW`, so the cut follows the link and truncates its target, a
  third file. With an absolute link, that file can lie outside the overlay. If a directory takes the id
  instead, the new line 725 raises `AttributeError` (no copy), which is not an `OSError`.
- **Evidence:**
  - `probe_idgap_symlink.py`: `adopted /deep/link kind symlink id 5` / `truncate returned (success)` /
    `at /data/big.bin: ('symlink', 5, 'size', 4)` / `other.bin: catalog size 50 bytes on source
    b'OOOO'`.
  - `probe_idgap_kind.py dir`: `truncate raised NON-OSError AttributeError 'NoneType' object has no
    attribute 'tier'`.
  - Base `0e6d14c` with the chain injected after `_serve` raises `FileNotFoundError` and leaves
    `other.bin` intact. `c3f31ff` behaves the same on the directory variant.
- **Severity note:** graded MEDIUM, because it needs the accepted purge-and-reuse chain inside one
  call's wait, plus a symlink. A strict reading of "a write that can follow a link out of the tier
  root" could grade it higher; Geoffrey's call. It is outside the plan-gate ruling, which accepted the
  gap for "a file", and it is unrecorded.

#### F6 [MEDIUM/CONFIRMED] Pre-existing: on the staged branch, a directory rename after the re-check leaves the cut's catalog writes keyed by a path the entry has left
- **Where:** `src/sosfs/engine/overlay.py:720` (the `nullcontext` branch), `:729-740`.
- **Failure scenario:** a stage-served file. As the stage `open_direct` returns, another thread renames
  `/data→/moved`. The bytes are cut, but `update_meta` and `update_residency` (keyed by `/data/s.txt`)
  match nothing. `entry.size` stays at the old figure and never heals.
- **Evidence:** `probe_unlocked_rename.py`:
  - `truncate returned`
  - `getattr size : 5` / `residencies : {'project': ('clean', 5), 'scratch': ('dirty', 5)}`
  - `stage tree : {'moved/s.txt': b'sm'}` / `read via overlay : b'sm'`
  - after flush, `getattr 5`, `assert_converged: FAILED`
  - The output is the same on `0e6d14c`, `c3f31ff` and HEAD.
- **Scope note:** a stage-served file is outside R1's WHEN clause, but this is a correctness defect in
  the branch the diff restructured, and the same class as F2 and F4. The seed (`:45-47`) calls this
  class "argued from the code, not reproduced"; it is now reproduced. As with F2 in cycle 1, Geoffrey may
  instead rule it out of this cycle; that needs a seed and a § Known open violations row.

#### F3 (reopened) [LOW/CONFIRMED] Two behaviour-changing mutants survive the full suite; E1 is not strictly equivalent
- **Mutant results:** killed: M01, M02, M04, M06–M14, M16, M17, M19, H1, H3. Strengthening only: M05 (always take
  the lock). Equivalent: H2 = E2. Survivors and E1:

| Mutant | Change | Targeted / full | Verdict |
|---|---|---|---|
| M20 | counter guard back to `not on_source` (`overlay.py:739`) | 103 / 493 passed | **Survivor.** On F2's interleaving the scratch counter stays 10 for a 4-byte copy, and 6 after eviction with no copy left (HEAD: 4, then 0). § 7 trusts these counters; the faster-copy test does not assert them. |
| M03 | `kind != 'file'` → `kind == 'dir'` (`overlay.py:716`) | 103 / 493 passed | **Survivor.** A handle-less truncate of an adopted symlink raises `IsADirectoryError` at HEAD. The mutant returns and empties the link target while the catalog says 10 (`probe_symlink_truncate.py`). The line is pre-existing and untested. |
| M18 = E1 | drop `previous is not None` | 103 / 493 passed | **Not strictly equivalent.** On F6's interleaving HEAD returns with stale sizes, and the mutant raises `AttributeError` at `:740`. It differs only where HEAD is already wrong; re-check it once F6 is settled. |
| H2 = E2 | drop `_held_path`'s `source_path is not None` | 103 / 493 passed | **Equivalent.** Every source residency the code writes carries a non-NULL `source_path`: adoption (`overlay.py:236-245`), `create` onto the source (`:366-371`), `transfer.copy`'s confirm (`transfer.py:116-117`), and the replay (`overlay.py:1069-1074`). The only state that tells them apart is a pre-`fe26962` crash mid-commit, and the crash gate (`lifecycle.py:100-102`) refuses that database. |

Records cross-check: no bare `file:line` cite in any added record line; all are pinned (`0a88f30`,
`c3f31ff`, `0e6d14c`, `fe26962`, `141aa86`). Both high-blast-radius tables only widened (`_held_path`
added). The seed's `open`/promotion reproductions hold at HEAD (`probe_seed_open.py`). The usage guard
at HEAD is correct (`probe_flip_counter.py`: 4, then 0). Lock order (path → source → SQLite) matches
§ 10. `mark_dirty` precedes the cut in both branches (§ 1; M12 killed). `rename` blocks on the source
lock while a locked cut runs (`probe_locked_rename.py`). PLAUSIBLE findings: none.

### Requirement → evidence matrix (cycle 2)

| R-ID | Verified by | How | Status |
|---|---|---|---|
| R1 | reviewer | 18 targeted tests pass, each R1 sequence converges; the re-decision line runs on all of them. F4–F6 fall outside R1's WHEN clause. | ✅ |
| R2–R4 | (not touched by the remediation) | R2/R3 tests pass in the R5 run; R4's records unchanged | ✅ (cycle 1) |
| R5 | reviewer | lint exit 0; 493 passed, 0 failed, 0 errors | ✅ |
| R6 | reviewer | 20/20 seeds, no `CI`, no `.hypothesis/` | ✅ |
| R7 | reviewer, from the run record | run 42342479853 on `7eba2ad`: four jobs `success`; post-`7eba2ad` diff outside `spec/` empty | ✅ |

### Routing to build (properties, not mechanisms)

- **F4, F5, F6 and F2's open domain: one class.** A handle-less `truncate` acts only on the entry it was
  asked for, as that entry stands when the bytes are cut and when the cut is recorded. Every decision
  the cut uses holds at that moment, or the call does not cut:
  - whether the source lock is held;
  - the entry's kind;
  - the serving copy;
  - the held place;
  - the keys its catalog writes use.

  It never truncates through a link, and never cuts the source outside the source lock (§ 10).
  **Structural fix first:** the serving decision is computed twice (`_serve` before the wait,
  `fastest_copy` inside it), and the lock choice is taken from the first. Decide once, under exclusion
  from every mover that can change the decision, and pass the result on. Then run the sweep below.
  **Completeness side, pinned alongside:** the GOAL's R1 sequences, uncontended, still never raise and
  still converge. A replay landing mid-call does not turn a file that exists into `ENOENT`. The
  plan-gate acceptance covers only a concurrent removal or ancestor rename. F6 is pre-existing:
  Geoffrey may rule it out of this cycle at the gate, with a seed and a § Known open violations row.
- **F1 — records.** After the fix, every record (§ 5, § 10, the `truncate` docstring, test comments, the
  seed, PLAN § Risks, the § Known open violations row) claims exactly what the code delivers. No
  "everything is decided again" unless the code does it. Any admitted case is recorded: the non-file
  identity-gap variant, if it stays open, and F6, if ruled out.
- **F3 — mutant sweep** over every decision in `truncate`'s no-handle branch and `_held_path`, re-run
  after the structural fix, under `-m unit` and `CI=true`, with equivalent mutants recorded and why. The
  faster-copy interleaving asserts the tier counters (M20). A handle-less truncate on a symlink is pinned
  (M03). E1 is re-judged once F6 is settled.
- This reopens `truncate`'s no-handle branch, which done phases built on; `code-build` retunes its gate
  (Step 1.3).

### Human-gate triggers (cycle 2)

**Triggered.** Every CONFIRMED finding sits in regions on `AGENTS.md` § Invariants → *High-blast-radius
paths (human gate)*, the `truncate` row and the `…, _held_path` row (that table is the only copy). F4 is
a CRITICAL § 10 violation. Geoffrey's sign-off on the final engine diff stands (GOAL Q4).

High-blast-radius regions the full branch diff touches: `engine/overlay.py` `truncate` (the no-handle
branch, rewritten), `_stat_held` and the new `_held_path`; `tests/engine/test_replay.py` (+432 lines);
and both high-blast-radius tables in `AGENTS.md` (widened by `_held_path`, nothing removed). No
`state/`, `transfer`, `writeback`, `evict`, `drain`, `locks` or `tier/` edit.

**Escalation:** this is the second review cycle without convergence, and the remediation introduced
F4. Per `code-review` step 6, the cycle stops for Geoffrey before another build round. A third review
is the last under the ≤3-cycle bound.

## Geoffrey's rulings on the cycle 2 escalation (2026-11-07)

- **Route:** restructure in one last build round, then the third and final scoped review. A
  handle-less `truncate` always takes the source lock, then decides once under it (with its path
  lock) every value the cut and its recording use: the lock held, the kind, the serving copy, the
  held place and the write keys. It refuses (ENOENT) when any of them changed during the wait, and
  never follows a symlink.
- **Severities:** F4 **HIGH** and F5 **HIGH** (real silent-damage paths reachable only through narrow
  concurrent interleavings).
- **F6** (pre-existing staged-branch size after a directory rename) goes out of this cycle, to a seed
  with its `ROADMAP.md` entry and a § Known open violations row.
- **F1, F3:** routed to build as written; F3's surviving mutants M20 and M03 get tests, and E1 is
  re-judged after the restructure.
- **Sign-off:** after the final review, on the final engine diff.

## Review cycle 3 — changes-requested (2026-11-07)

- **Reviewed commit:** `aafdd44`  ·  **Base:** `main` (`origin/main` at `0e6d14c`)  ·  **Cycle:** 3 of ≤3 (final)
- **Mode:** **scoped to verifying the remediation** of the named findings, per Geoffrey's cycle-2
  escalation ruling ("restructure in one last build round, then the third and final scoped review"):
  F4, F5, F2's open domain, F1 (reopened), F3 (reopened: M20, M03, E1), F6's disposition, and any
  regression in the touched code. Remediation diff `git diff ea38083 HEAD -- . ':(exclude)spec/'`
  (7 files, +381/−59), with the full spec-excluded diff from `origin/main` for context. Fresh blind
  subagent (opus, effort `xhigh`); sequential fallback not used.
- **Ground truth handed over:** GOAL.md and Geoffrey's ruled property, verbatim: "A handle-less
  `truncate` always takes the source lock, then decides once under it (with its path lock) every value
  the cut and its recording use: the lock held, the kind, the serving copy, the held place and the write
  keys. It refuses (ENOENT) when any of them changed during the wait, and never follows a symlink."
- **Inputs to the reviewer:** GOAL.md, the rubric, `AGENTS.md`, the two diffs and the one-line log, and
  an excerpt of this file (cycle 1 findings and routing, cycle 2 remediation status through routing,
  and every ruling). No PLAN/TECH content, builder report or KB text. Blindness slips: none reported.
  One question was put to it as a question: whether holding the source lock across stage I/O and a
  promotion is consistent with § 10.
- **Contract drift:** none (`git log origin/main..HEAD -- spec/…/GOAL.md`: `be881e1`, `0a88f30` only).
- **Artifact R-IDs:** none. Every R-ID is gradeable from the spec-excluded diff; the reviewer graded all.

### Verification run

Probes, mutant trees and logs live in the worktree's gitignored `.local/review3/` (`probes/`, `mut/`,
`logs/`, `trees/{HEAD,ea38083,origin_main}` as `git archive` copies). Probes run as
`PYTHONPATH=<tree>/src:<tree> uv run python .local/review3/probes/<probe>.py <variant>`; concurrency is
injected deterministically (the suite's `Interleaved`, or a helper thread gated by events at a hooked
call).

- R5: `tools/lint.sh` exit 0; `CI=true uv run pytest -n auto -m unit` → **500 passed in 26.90 s**, 0
  failed, 0 errors (> 474). `--collect-only`: 510 total, 500 unit, 10 integration, 162 engine; matches
  the counts `AGENTS.md` states.
- R6: `.hypothesis/` (38 files) moved aside with a manifest; `CI` unset; seeds 1–20, each from a tree
  with no `.hypothesis/` → **20/20 "1 passed"**. `test_model.py` unchanged.
- R7, from `.local/ci/run.json` and `.local/ci/logs/`: run 42409264319 on `1a8be2d`, all four jobs
  `success` (`Unit (Linux, 3.15)` and `3.15t` 500 passed; `Unit (macOS, engine-only)` 456 passed, 44
  skipped). `git diff --stat 1a8be2d HEAD -- . ':(exclude)spec/'` is empty, so CI's tree is HEAD outside
  `spec/`.
- R1: the six R1 tests and the swap test pass by name, each ending in `assert_converged`; against
  `origin_main` engine code the same six fail with `FileNotFoundError` (not hollow).
- Mutant sweep: 33 mutants over `truncate`'s no-handle branch and `_held_path`, one tree each, targeted
  (110 tests) then full `CI=true -n auto -m unit` for survivors; control M00 110 / 500 passed.
- Lock order: the full suite under a lock-order plugin (`probes/lockorder_plugin.py`) counted 108–142
  handle-less-truncate source-lock acquisitions and 0 engine-code violations.
- Greps: no spec R/P ids in `src/` or `tests/` (`grep -rnE '\b[RP][0-9]+\b' src tests` exit 1); no
  `src/sosfs/state/` or `SCHEMA_VERSION` edit.
- **Orchestrator re-ran** at `aafdd44` `probe_f5_kind.py oob_final`, `probe_f6.py
  past-res`, `probe_m11.py` and `probe_m27.py` on the HEAD tree and on the M11 and M27 mutant trees.
  Output identical to the reviewer's (quoted below).
- Hygiene: `git status --porcelain` empty after hand-back (checked by the orchestrator), HEAD still
  `aafdd44`. `.hypothesis/` restored (before/after/final manifests `cmp` identical); the 20 per-seed
  `.hypothesis` dirs are in `.local/review3/moved-aside/`; `-p no:cacheprovider` throughout.

### Remediation status

| Item | Status at `aafdd44` |
|---|---|
| F4 (HIGH per ruling) cut reaches the source unlocked | **Fixed.** `probe_f4_flip.py`: `lockprobe` → `('scratch','/data/s.txt','lock held')`; `big` → source cut `lock held`; `flush` converges; `wrongobj` → ENOENT. Same interleaving on `ea38083`: `lock free to others`. Nine movers started inside the window (flush, round trip, wrong-object sequence, evict, commit, unlink, create onto the source, scheduler poll) all report `helper finished while the cut held its locks: False` and converge. |
| F5 (HIGH per ruling) kind not re-checked | **Fixed.** Symlink, absolute symlink, staged and directory variants all raise `FileNotFoundError`; `other.bin` stays 50 bytes, the outside file 30. On `ea38083` the symlink variant cut `other.bin` to 4 and the directory variant raised `AttributeError`. A link at the held place gives ELOOP. The unqualified "never follows a symbolic link" record overstates this (F1a). |
| F2's open domain | **Met.** Every value used after the wait is decided under the lock (`overlay.py:723-737`: entry, kind and id, serving, held place, upstream and mark target, previous residency); the pre-wait `entry` feeds only the id comparison. Stale-serving interleaving: `{'project': ('clean', 10), 'scratch': ('dirty', 4)}`, usage `(4,1)` then `(0,0)`, reads `b'0123'`, converges (3-tier variant too). |
| F3 (LOW) M20, M03, E1 | **M20 and M03 killed; E1 re-judged; two new survivors (F3 below).** M20's analog (counter guard from the pre-wait copy) dies on the faster-copy test (`assert (10, 1) == (4, 1)`); old M03 dies on `test_truncate_without_a_handle_of_a_symlink`. E1 (M19) differs from HEAD only inside F6's ruled-out window (HEAD stale sizes, mutant `AttributeError`): equivalent outside it. |
| F1 (HIGH) records | **Still open (F1 below).** The file identity gap, the non-file refusals, "takes the lock for every cut" and § 10's promotion-hold sentence match the code (`probe_identity_gap.py`: `returned (no error)`, `same: True`, converged). Two claims still overstate. |
| F6 disposition (ruled out) | **Seed, `ROADMAP.md` entry and § Known open violations row exist; the narrowing claim holds; the record misstates the effect (F1b).** `probe_f6.py review` (rename as the stage open returns): HEAD `rename finished while the cut held its locks: False`, getattr 2, converged; `ea38083` and `origin_main` getattr 5, FAILED. `past-open` matches the seed's numbers exactly. `past-source`, which the seed calls argued, reproduces (getattr 1000 for a 2-byte object after flush). |

### Findings (cycle 3)

#### F1 (reopened) [HIGH/CONFIRMED] Two records still claim more than the code delivers
Text-only. HIGH by the rubric's letter (stale § Invariants text); both items are narrow, and Geoffrey may
grade it down as he did F2.

- **(a) "Never follows a symbolic link" holds for the last path component only.**
  - Claimed without qualification at `AGENTS.md:406-407` (§ 5), the `truncate` docstring
    (`overlay.py:705`), `tests/engine/test_replay.py:39`, root `PLAN.md:582` and
    `issues/source-served-open-race.md:67`.
  - The cut's guard is `os.O_NOFOLLOW` (`overlay.py:732`), which is defined on the last path component.
  - Evidence, re-run by the orchestrator: `probe_f5_kind.py oob_final` (link at the last component):
    `OSError 62 … Too many levels of symbolic links`.
  - Geoffrey's property says "never follows a symlink": whether it means the last component (a
    records fix) or every component (a code change) is his call (human gate below).
  - *2026-11-08: evidence trimmed at publish, on Geoffrey's ruling (§ Geoffrey's rulings on the cycle 3
    escalation).*
- **(b) The F6 window's record says the recording "matches nothing"; the counter write matches.**
  - `adjust_usage` (`overlay.py:741-742`) is keyed by tier, not path. When the ancestor's catalog move
    lands between `get_residency` (`:737`) and the transaction (`:738`), the stage counter moves and the
    residency row does not.
  - Evidence, re-run by the orchestrator: `probe_f6.py past-res` → `usage scratch: (2, 1)` with
    residency `('dirty', 5)`; after flush and eviction `evict scratch: True -> usage scratch (-3, 0)`;
    `assert_converged: FAILED`. A negative counter is a § 7 effect; the § Known open violations row is
    tagged § 5, 10 only.
  - Records saying "matches nothing / no row": `AGENTS.md:408-410` and `:666` (the row),
    `overlay.py:707`, `issues/truncate-recording-after-ancestor-rename.md:16-22` (lists only
    `update_meta` and `update_residency`), `ROADMAP.md:64`, root `PLAN.md:592-594`,
    `issues/source-served-open-race.md:52`.
  - Also unrecorded in the same window: `past-serve` → `EngineError No resident copy of /data/s.txt`;
    `past-promote` → `LookupError No such entry: /data/s.txt`, leaving an unresolved `promote` intent
    and stage bytes with no residency (`assert_converged` fails). The seed (`:21-22`) says nothing heals
    the residency size "short of another write or `truncate`"; a read-only open and release heals it
    (`('dirty', 5)` → `('dirty', 2)`, re-run by the orchestrator).
  - All pre-existing: `origin_main` and `ea38083` give identical output (`logs/f6_base.log`).

#### F3 (reopened) [LOW/CONFIRMED] Two behaviour-changing mutants survive the full suite
- **M11** `_serve(current)` → `_serve(entry)` (`overlay.py:726`): 110 / 500 passed. `probe_m11.py` (the
  file's own queued rename replays during the wait, then the cut promotes): HEAD `truncate returned`,
  converges; M11 `truncate raised TierError No file at /s.txt in tier project` on a file that exists
  (orchestrator re-ran both). This is Geoffrey's property directly (the promotion reads the entry found
  under the lock), and no test pins it.
- **M27** `get_residency(path, serving)` → the source tier (`overlay.py:737`): 110 / 500 passed.
  `probe_m27.py` (uncontended: dirty 8-byte stage copy over a 5-byte source copy, cut to 2): HEAD usage
  `(2, 1)`, then `(0, 0)` after eviction; M27 `(5, 1)`, then `(3, 0)` (orchestrator re-ran both). The line
  is pre-existing; no test cuts a dirty stage copy whose size differs from the source's and checks the
  counter.

PLAUSIBLE findings: none.

**Mutant table** (33 mutants; `mut/results.jsonl`). Killed: M01–M10, M12–M18, M20′–M26, M28, H1, H3, H4
(M04 no source lock: 12 failed; M05 the F4 shape: 3 failed; M17 dirty mark after the cut: 1 failed; M18
drop `O_NOFOLLOW`: 1 failed). Survivors: **M11, M27** (F3). Equivalent: M19 = E1 outside F6's ruled-out
window; H2 = E2 (every source residency the code writes carries a non-NULL `source_path`, and the
remediation touches none of those writers).

**§ 10 question (holding the source lock across stage I/O and a promotion):** consistent with § 10 and
§ 1. The lock-order run found no `locks.hold`, `_ensure`, `getattr` or `readdir` under the source lock,
no acquisition inside `db.transaction()` or under `_handles_mutex` in engine code (10 hits, all the test
helper `unlink_during_next`, by design). `probe_promote_hold.py` (promotion put slowed to 1 s): create,
rename, flush, release of a written source-served handle, `commit_upstream` to the source, a scheduler
poll and another handle-less truncate all finish after the truncate returns; a plain `getattr` is not
blocked, matching § 10's sentence at `AGENTS.md:572-573`. `mark_dirty` still commits before the cut
(M17 killed), and the promotion stays intent-bracketed.

**Reading of the ruling (not a finding):** "refuses when any of them changed during the wait" applies to
the values observed before the wait (identity and kind). The serving copy and held place are decided only
under the lock, so a faster copy or a flip is followed, not refused; this matches the cycle-1 F2 ruling.

**Scope creep:** none; every change maps to a ruling or a measured count.

**Out of Scope:** one hardening item on tier path resolution outside `truncate`, reached only by
out-of-band change to a tier tree. The profile's security lane is on, so it is held for Geoffrey's
triage and not described here. The unresolved `promote` intent in F6's window is F6's domain (F1b).

### Requirement → evidence matrix (cycle 3)

| R-ID | Verified by | How | Status |
|---|---|---|---|
| R1 | reviewer | six R1 tests + swap test pass and converge; fail on `origin_main` engine code | ✅ |
| R2 | reviewer | both `test_size_through_a_bypass_stage_*` pass | ✅ |
| R3 | reviewer | `assert_converged` in every R1/R2 test | ✅ |
| R4 | reviewer | records untouched by the remediation; § Known open violations names `issues/audit-command.md`, not this seed | ✅ |
| R5 | reviewer | lint exit 0; 500 passed, 0 failed, 0 errors; counts match `AGENTS.md` | ✅ |
| R6 | reviewer | 20/20 seeds, `CI` unset, no `.hypothesis/` | ✅ |
| R7 | reviewer, from the run record | run 42409264319 on `1a8be2d`, four jobs `success`; no non-`spec/` change since | ✅ |

Every R-ID is met. The open items are records (F1) and coverage (F3); no engine defect was found.

### Routing (escalation, not a build round)

This is the third and final review under the ≤3-cycle bound and it does not converge clean, so it does
**not** route to another `code-review`↔`code-build` loop. It stops for Geoffrey (`code-review` step 6).
If he chooses a last fix, the routed properties are:

- **F1 — records claim exactly what the code delivers.** (a) The symlink claim states the components it
  covers, or the code covers every component; which one is Geoffrey's reading of his property. (b) The
  F6 window's record names every write the recording makes and its effect (the stage counter moves while
  the residency does not, and can go negative after eviction, a § 7 effect), the earlier landings
  (`EngineError`, `LookupError` with an unresolved `promote` intent), and corrects the healing claim.
- **F3 — tests for the two survivors:** a cut whose own queued rename replays during the wait and then
  promotes (kills M11); a handle-less cut of a dirty stage copy whose size differs from the source copy's,
  asserting the stage counter before and after eviction (kills M27).

### Human-gate triggers (cycle 3)

**Triggered.** Both CONFIRMED findings sit in regions on `AGENTS.md` § Invariants → *High-blast-radius
paths (human gate)* (the `truncate` row and the `…, _held_path` row; that table is the only copy), and
F1 is § Invariants text. Geoffrey's sign-off on the final engine diff stands (GOAL Q4). His calls:

1. The escalation itself: how to close F1 and F3 after the final review.
2. F1's severity (HIGH by the letter, text-only).
3. F1a: whether "never follows a symlink" in his ruled property means the last component or every
   component.
4. The out-of-Scope tier-resolution hardening item: seed it in the security lane, or drop.
5. Sign-off on the engine diff (`src/sosfs/engine/overlay.py`, +45/−23 against `origin/main`).

## Geoffrey's rulings on the cycle 3 escalation (2026-11-08)

Relayed on the foreman's board (`answer`, signed, 2026-11-08), all five as the foreman recommended:

1. **Closing route (a):** a text-and-tests spot fix with no new review: F1's records text and pinning
   tests for M11 and M27 (F3). Then sign-off and publish.
2. **F1 is MEDIUM.**
3. **F1a:** "never follows a symlink" means the last path component. Records fixed to say so; no code
   change.
4. **The out-of-Scope tier-resolution item** is seeded in the security lane. F1a's evidence was
   trimmed here before publish.
5. **Sign-off:** Geoffrey signed off on the final engine diff in `src/sosfs/engine/overlay.py` (GOAL Q4)
   as the escalation described it: the handle-less `truncate` always takes the source lock, looks the
   entry up again under it (ENOENT on a missing entry, a changed id or a non-file), decides the serving
   copy, held place and upstream once, marks dirty, opens `O_WRONLY | O_NOFOLLOW`, cuts, and records meta,
   residency and the stage counter in one transaction.

### Spot fix (no review, per ruling 1)

- `4d5f198` (P1: F1 F3) and `cb50f3b` (P2 seeds), built by a fresh build subagent:
  - **F1(a):** `AGENTS.md` § 5, the `truncate` docstring, `tests/engine/test_replay.py`'s module docstring,
    root `PLAN.md` § Risks and `issues/source-served-open-race.md` now say the open does not follow a
    symbolic link at the file itself, the last path component (`O_NOFOLLOW`).
  - **F1(b):** the F6 window's record (`AGENTS.md` § 5 and the § Known open violations row, now tagged
    § 5, 7, 10; the docstring; `ROADMAP.md`; root `PLAN.md`; both cycle seeds) names the stage-counter
    write that lands while the catalog writes match no row, the § 7 effect after eviction, the earlier
    `EngineError` and `LookupError` landings, and the corrected healing claim.
  - **F3:** `test_truncate_without_a_handle_promotes_from_where_a_replay_during_the_wait_moved_the_file`
    kills M11; `test_truncate_without_a_handle_counts_the_stage_copy_it_cuts` kills M27. Each passes on
    HEAD and fails on its mutant, while the old suite passes on both mutants (500/500):
    `remediation/cycle3-pinning-tests.md`.
- `src/` changed in the `truncate` docstring only; the signed-off code is byte-identical.
- Gates: P1 (`regressions mutants records suite`) and P2 (`suite seeds`) green; profile `verify` lint exit
  0, **502 passed**, 0 failed; seeds 1–20 green; `ledger prepush` green on `cb50f3b`.
- **Foreman's check (orchestrator):** the `src/` diff from `8517e7f` is the docstring hunk only; the trimmed
  evidence and § Spot fix are the foreman's. CI on `cb50f3b` is P3 Run B's.
