# GOAL — A handle-less `truncate` records its cut on the file an ancestor's rename has moved

> **Origin spec.** The *what* and *why* — the locked contract `code-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `code-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** truncate-recording-after-ancestor-rename
- **kind:** fix
- **appetite:** small

## Problem

**A handle-less `truncate` can cut a file's bytes and then fail to record the cut.** The catalog
keeps reporting the old size, the stage's usage counter drifts from the residencies it counts, and
the tree does not converge after a flush. It is silent: the call returns success. The defect is
pre-existing (`escaped`: `pre-factory`, Clarifications).

The cut is a write that the catalog must record. Since `source-path-truncate-and-stat` (`6425a21`),
it holds the file's path lock and then the source lock from its decision through its recording
(`src/sosfs/engine/overlay.py:735-785` at `630a406`). Under them it looks the entry up again at its
path, decides the serving copy (`_serve`, promoting first when a faster tier admits the file), the held
place and the upstream, marks the copy dirty, cuts, and records. The dirty mark (`journal.mark_dirty`,
`:774`) and the recording's catalog writes (`catalog.update_meta` and `catalog.update_residency`,
`:782-783`) are keyed by the path the caller gave. The recording's third write, the stage counter
(`registry.adjust_usage`, `:784-785`), is keyed by tier.

Neither lock covers the file's ancestors. A directory `rename` holds the two directory paths' locks
(`overlay.py:427`). It takes the source lock only inside `_vacate` (`:444`, `:1243`), then releases it
and moves the whole subtree in the catalog (`:446-447`, `catalog.rename`) and on every stage (`:462`,
`tier.rename`). A rename that has passed its `_vacate` when the cut takes the source lock is therefore
not held off. When its catalog move lands after the cut has read its keys and before the cut records:

- **the catalog writes match no row.** The bytes are cut, but `entry.size` and the cut copy's
  residency `size` keep the old figure. After a flush, `getattr` still reports it and
  `assert_converged` fails;
- **the counter write lands.** The stage's counter moves by the cut's size change while the residency
  does not. Eviction subtracts the residency's figure and leaves the counter off by that change,
  negative after a cut that shrank the copy. Eviction trusts these counters (`AGENTS.md` § 7);
- **a read-only open and release** heals the residency `size` and moves the counter by the same change
  a second time, so the counter still ends that far off. `entry.size` heals only on another write or
  `truncate`.

Landing earlier in the same window, the move makes the call raise a non-`OSError` or leak an
intent:

- `_serve` raises `EngineError` when `fastest_copy` reads the old path (`overlay.py:681-683`);
- `mark_dirty` raises `LookupError`, because no entry is at the path (`state/journal.py:131`, via
  `_entry_id`);
- a promotion off the source raises `LookupError` after its copy. The copy records its residency by
  path (`engine/transfer.py:105-118`), outside the `try` that rolls the intent back, so the `promote`
  intent stays unresolved and the stage holds the bytes under no residency.

The stage open may also raise `ENOENT`, which the `source-path-truncate-and-stat` plan-gate ruling
accepts for an ancestor renamed mid-call.

**Evidence** (from the seed, with drift re-checked at `630a406`):

- **The recording** (that cycle's review cycle 2, F6, at `ea38083`). On a stage with
  `bypass_size=100` and a 5-byte `data/s.txt` that the stage serves, a rename of `/data` to `/moved`
  ran in the cut's own thread as the stage `open_direct` returned. The source lock is re-entrant, so
  this models a rename already past `_vacate`. The call returned; `getattr` reported 5; the residencies
  were `{'project': ('clean', 5), 'scratch': ('dirty', 5)}`; and `assert_converged` failed after a
  flush.
- **The rename on another thread.** The same rename started on another thread at the same point now
  waits in `_vacate` and converges: `getattr` 2, residencies `('clean', 5)` and `('dirty', 2)`. A cut
  served off the source records by the same keys. That is argued from the code, not run.
- **The counter** (review cycle 3, F1, at `aafdd44`). The stage counter read `(2, 1)` (bytes, files)
  with the residency `('dirty', 5)`. After a flush and the copy's eviction it read `(-3, 0)`. An
  open and release made the counter read `(-1, 1)`, and eviction still left `(-3, 0)`.
- **The earlier landings** (`_serve`'s `EngineError` and the promotion's `LookupError`) were the same
  at `0e6d14c` and `ea38083`. They are pre-existing.
- **Drift since the seed.**
  - `source-served-open-race` (`5b8dcaf`) made a promotion off the source look the entry up again
    under the source lock (`_lookup_again`, `overlay.py:695`) before it opens an intent. So a move
    landing before that lookup now raises `ENOENT`, with no intent. A move landing during the copy is
    the `LookupError` case above, read from the code at `630a406` and not re-run.
  - The counter guard the seed cites at `overlay.py:742` (`6425a21`) is now at `:784`.

## Outcome / vision

A handle-less `truncate` that a concurrent rename of one of the file's ancestors overlaps has two
possible outcomes. Either it records its cut on the file it cut, wherever the rename has moved that
file, or it raises `ENOENT` having cut nothing. The sizes the catalog reports match the bytes. Every
stage's counter agrees with its residencies. The tree converges once the queue drains. No such overlap
makes the call raise `LookupError` or `EngineError`, or leave an intent unresolved. The records say
exactly what is closed, and what stays open has an owner.

The profile's `verify` stays fully green, under `CI=true` and under random seeds, and CI's four jobs
conclude `success`.

## Acceptance criteria (the contract)

These terms hold throughout:

- *The cut* is a handle-less `truncate(path, length)` on a file. *Its lookup under the lock* is the
  `catalog.get(path)` it makes once it holds the source lock (`overlay.py:766`).
- *An ancestor's rename* is a user `rename` of a directory that contains the file, at any depth. It
  *lands* when its catalog move commits.
- *The file's path* is wherever the catalog holds the file's entry once the call returns.
- *A counter agrees* with its tier when the tier's `registry` usage `(bytes, files)` equals the sum of
  the `size` of that tier's residency rows and their count.
- *The queue drains* means `journal.queued() == []` and `journal.pending() == []`.
- *Converges* means `tests/engine/test_replay.py::assert_converged` passes.
- Counts are the numbers pytest prints in its summary line, asserted as numbers (`AGENTS.md`
  § Verification).
- Every criterion is graded over interleavings in which the file itself is not removed during the call
  (Non-goals: the identity gap).

The criteria:

- **R1 — the recording.** WHEN an ancestor's rename lands after the cut's lookup under the lock and
  before the cut returns, the call SHALL do one of two things:
  - return, with `getattr` at the file's path reporting `length`, the cut copy's residency `size`
    reading `length`, and reads of the file returning its first `length` bytes;
  - raise `ENOENT`, with no bytes of any file cut.

  Each of these SHALL satisfy it:
  - a file a stage serves (the seed's F6 stack);
  - a file served off the source;
  - a rename on another thread, and a rename already past its `_vacate`.

  (Today the call returns, `getattr` and the residency keep the old size, and the tree does not
  converge after a flush.)
- **R2 — the counter.** WHEN the call in R1 returns, every stage's counter SHALL agree. It SHALL
  still agree after the queue drains, after the cut copy's eviction, and after a read-only open and
  release of the file. No counter SHALL read negative. (Today the counter reads `(2, 1)` beside a
  5-byte residency, and `(-3, 0)` after eviction.)
- **R3 — the earlier landings.** WHEN an ancestor's rename lands after the cut's lookup under the
  lock and before the cut opens its copy, the call SHALL either satisfy R1 and R2, or raise an
  `OSError`. That window covers the serving decision, a promotion off the source (including during
  its copy) and the dirty mark. The call SHALL NOT raise `LookupError` or `EngineError`. Afterwards,
  no `promote` intent SHALL be unresolved, and no stage SHALL hold bytes for the file without a
  residency for them. (In this cycle, per Q2.)
- **R4 — the property**, gradeable by reading the code. From the cut's lookup under the lock to the
  commit of its recording, every catalog read and write the call makes for the file SHALL reach the
  entry that lookup returned, whatever path a rename gives it meanwhile. That covers the serving copy,
  a promotion's recording, the dirty mark, the residency the counter change is computed from, and the
  meta and residency the cut records. (Design families per Q1.) The handle-less `truncate` SHALL keep the property Geoffrey signed off on 2026-11-08
  (Constraints).
- **R5 — the counter guard.** The recording's `previous is not None` guard (`overlay.py:784`) SHALL
  either be pinned by a test with a mutant that drops it and turns that test red, or be removed
  because the fix leaves it unreachable. The plan SHALL say which, and why. (The seed's note; the guard
  was left unpinned in `spec/source-path-truncate-and-stat/remediation/cycle2-mutant-sweep.md`, E1,
  because a pin would have asserted the stale size this cycle fixes.)
- **R6 — the tests.** Every sequence in R1, R2 and R3 SHALL be pinned by a deterministic
  injected-interleaving test in `tests/engine/test_replay.py`, under `-m unit`. Each test SHALL
  converge once the queue drains, and each SHALL have a fix-removal mutant that turns it red. The
  existing tests in `tests/engine/test_replay.py` SHALL pass unchanged, including the handle-less
  `truncate`'s and `open`'s.
- **R7 — the records**, in the same commit as the code they describe, saying exactly what the code
  delivers and no more:
  - `AGENTS.md` § 5 SHALL say how the cut now records under an ancestor's rename. So SHALL § 10, if a
    lock's holders or hold change. The `truncate` docstring (`overlay.py:736-751`) SHALL agree with
    that text, and so SHALL the docstring of any other function whose behaviour changes.
  - § Known open violations SHALL drop the row this seed owns (`AGENTS.md:682`), or keep only what
    stays open, naming a seed that outlives this one.
  - Root `PLAN.md` § Risks (`PLAN.md:595-598`) SHALL agree. So SHALL
    `issues/freethreaded-concurrency-stress.md`'s "Path locks do not cover descendants" bullet, if the
    fix changes what it says.
  - Q3's home SHALL be written in this cycle.
  - The measured test counts SHALL match the final tree in § Commands (`AGENTS.md:70`), § Repository
    map (`:111`), § Conventions (`:657`) and § Verification (`:700`).
  - Once the roadmap step has retired the seed and its `ROADMAP.md` entry, no file outside `spec/`
    SHALL name `issues/truncate-recording-after-ancestor-rename.md`.
- **R8 — the gate.** All of these SHALL hold:
  - WHEN the profile's `verify` (`tools/lint.sh && uv run pytest -n auto -m unit`) runs with
    `CI=true` on macOS, `tools/lint.sh` SHALL exit 0.
  - In that run, pytest SHALL report 0 failed and 0 errors. Its passed count SHALL exceed the count the
    same command reports at `630a406` (§ Verification records 521 unit tests there).
  - WHEN `uv run pytest tests/engine/test_model.py` runs on macOS without `CI` set, from a tree with no
    `.hypothesis/` directory, once for each `--hypothesis-seed` from 1 to 20, every run SHALL pass.
  - WHEN the CI workflow runs on this cycle's final tree, each of `Lint & build`, `Unit (Linux, 3.15)`,
    `Unit (Linux, 3.15t)` and `Unit (macOS, engine-only)` SHALL conclude `success`, as read from the
    run record.

## Non-goals (no-gos)

- **The identity gap.** If the file is removed and purged during the call, a new file can take its id
  and a rename can carry that file onto the path. The cut then acts on the new file. This cycle does
  not close that, and closing it needs an identity token, which is a schema change. Home:
  `issues/entry-identity-token.md`.
- **Other calls with the same shape.** `evict_copy`, `_set_meta` and others hold a path lock and
  write the catalog by that path. Home: `issues/freethreaded-concurrency-stress.md` (Q3).
- **`open`'s windows under an ancestor's rename.** These are `_serve`'s `EngineError` before
  `fastest_copy`, and an ancestor's round trip between `open`'s tier decision and its act. Home:
  `issues/freethreaded-concurrency-stress.md` ("Path locks do not cover descendants", and "An
  ancestor's round trip"). Any change to `_serve` shared with `open` is still graded against cycle 4's
  properties (Constraints).
- **Making path locks cover descendants** in general. Home:
  `issues/freethreaded-concurrency-stress.md`.
- **`EngineError`, `LookupError` or `TierError` escaping the VFS API** on any path but the cut's call
  under R3. Home: `issues/engine-error-mapping.md`.
- **Narrowing how long anything holds the source mutex.** Home:
  `issues/source-commit-staged-publish.md`.
- **Exercising the race under real threads** (stress, free-threaded). Home:
  `issues/freethreaded-concurrency-stress.md`.
- **A mount-level reproduction and the integration lane.** Coverage is engine-level, under `-m unit`,
  on tmpdir tiers. Home: `issues/ci-integration-lane.md`.

## Constraints

- **`AGENTS.md` § Invariants § 1, § 5, § 7 and § 10 hold as written.**
  - That includes the lock order: path locks, then the source mutex, then `_handles_mutex`, then the
    SQLite write lock.
  - Nothing holding the source mutex calls `locks.hold`, `_ensure`, `getattr` or `readdir`.
  - Nothing acquires the mutex inside `db.transaction()`.
  - The dirty mark still commits before the bytes are cut (§ 1, metadata before acknowledgment).
  - A design that bends any sentence of these sections goes in the plan's deviation table, before
    Geoffrey, and into the same diff's § Invariants text.
- **The handle-less `truncate` keeps the property Geoffrey signed off on 2026-11-08.** It is item 5 of
  "Geoffrey's rulings on the cycle 3 escalation" in `spec/source-path-truncate-and-stat/REVIEW.md`.
  The call always takes the source lock and looks the entry up again under it. It raises `ENOENT` on a
  missing entry, a changed id or a non-file. It decides the serving copy, held place and upstream
  once, marks dirty, opens `O_WRONLY | O_NOFOLLOW`, cuts, and records meta, residency and the stage
  counter in one transaction.
- **`open` and `_serve` keep cycle 4's properties.** These are `spec/source-served-open-race/GOAL.md`
  R3 and R4. Its tests stay green unchanged (R6).
- **No state-database schema change.** There is no `SCHEMA_VERSION` bump. If the plan finds it cannot
  avoid one, it stops and asks, and the work moves to `issues/entry-identity-token.md` (the size
  circuit breaker).
- **No edit to `tests/engine/test_model.py`.** Hypothesis's fixed CI draw is a digest of that machine
  class's source, so any edit redraws the CI sample.
- **Same-commit records** (R7, `AGENTS.md` § Conventions).
- **Human sign-off at review** on the engine diff, unconditionally (Q4): Geoffrey signs off on the final
  `truncate` diff, and on `rename` or `transfer.copy` if the plan touches them, whatever the review finds.

## Clarifications

Resolved:

- **Q:** Kind and appetite? — **A:** `fix`, `small`, as the seed's frontmatter gives them. The seed is
  `status: unshaped`, so its draft R-IDs are input, not the contract. There are eight criteria, one of
  them the gate and two of them records and tests, for one deliverable: the cut and its recording. So
  the size circuit breaker does not trip. That holds if Q2 keeps R3 to the cut's own call and if no
  identity token is needed (Constraints). (recorded 2026-11-08, shaper)
- **Q:** Where did the defect come from (`escaped`)? — **A:** `pre-factory`.
  - At `630a406` the faulty lines (the recording's path-keyed writes, `overlay.py:774` and `:782-785`)
    blame to `6425a21`, the `source-path-truncate-and-stat` landing. That commit rewrote them under the
    source lock but kept their path keys, and its review found the defect pre-existing (F6, the same at
    `0e6d14c` and `c3f31ff`).
  - At `6425a21^` the same writes blame to `4aea975` and `3a984b2`, and the rename's unlocked catalog
    move to `437e27f`. All three are dated 2026-07-02 ("WIP: Phase 4 — engine …"), are ancestors of
    the factory adoption `6a046b4`, and carry no `spec/`.
  - Recorded with `metrics.py escaped … --from pre-factory`. Correct it at the gate if that reading is
    wrong.

  (recorded 2026-11-08, shaper)
- **Q:** Does the seed's draft contract carry over? — **A:** As input only. It maps as follows:
  - Its R1 splits into R1 (the recording, with an `ENOENT` outcome admitted for designs that hold the
    rename off) and R2 (the counter, with the open-and-release healing path and eviction).
  - Its R2 becomes R3, pending Q2.
  - Its R3 becomes R6, with convergence added.
  - The note on the counter guard becomes R5.
  - R4, R7 and R8 are new: the gradeable property, the records, and the gate.

  (recorded 2026-11-08, shaper)

Ruled at the GOAL gate:

- **Q1 — Which design families does R4 admit?** — **A:** (C). Either keyed by identity, where each read
  and write resolves the entry by the id the lookup under the lock returned, inside the transaction that
  makes it; or held off, where the rename's catalog move cannot commit while a cut holds the source lock.
  The plan chooses and justifies. Excluded: taking the ancestors' path locks, and re-resolve-and-retry
  loops. A held-off design widens a § 10 holder, and a design keyed by identity that reaches `transfer.py`
  touches coupled core. Either goes in the plan's deviation table. (ruled 2026-11-08, foreman as gate judge)
- **Q2 — Is R3, the earlier landings, in this cycle?** — **A:** (A), yes, scoped to the cut's own call:
  the serving decision, a promotion the cut triggers (including its copy), and the dirty mark. The cut
  already holds the source lock across that span, so this is one window. A leaked `promote` intent with
  stage bytes under no residency breaks § 1's bracket. `open`'s windows stay out (Non-goals). (ruled
  2026-11-08, foreman as gate judge)
- **Q3 — Where do the other same-shape calls go?** — **A:** (A). A dated line under the "Path locks do
  not cover descendants" bullet in `issues/freethreaded-concurrency-stress.md`, written in this cycle (R7),
  naming `evict_copy`, `_set_meta` and any other call the plan finds that holds a path lock and writes
  the catalog by path. Not brought in: several call sites would trip the size circuit breaker. (ruled
  2026-11-08, foreman as gate judge)
- **Q4 — Does Geoffrey sign off at review unconditionally?** — **A:** (A), yes. He signs off on the final
  engine diff (`truncate`, and `rename` or `transfer.copy` if the plan touches them) whatever the review
  finds, as for cycles 2, 3 and 4. (Geoffrey, 2026-11-08, in the foreman's session)
- **Q5 — What meets R1's "a rename already past its `_vacate`"?** — **A:** A rename on another thread
  stopped past its `_vacate`. The seed's same-thread injection is retired as evidence: under a design that
  holds the source lock across the rename, it re-enters the lock, and production cannot reach that state,
  because nothing inside the cut calls `rename` and each FUSE operation runs on its own thread. (ruled
  2026-11-08, foreman at the plan gate, PLAN deviation row 2)
- **Q6 — What is R6's "fix-removal mutant" for a sequence the baseline already gets right?** — **A:**
  A mutant of the fix's own decisions that turns the test red, each with its named cause. A test whose
  sequence passes on the baseline (a pin) cannot be turned red by removing the fix, so its mutant removes
  or inverts a decision the fix makes instead. Every R6 test still needs a mutant that turns it red.
  (ruled 2026-11-08, foreman at the plan gate, PLAN deviation row 3)
- **Q7 — Does R7 cover records its list does not name?** — **A:** Yes. R7's "say exactly what the code
  delivers" covers every record the change makes untrue. That includes `rename` joining both
  high-blast-radius tables, the engine-test count, `tests/engine/test_replay.py`'s module docstring, and
  the stress seed's replay bullet that the hold closes, stated as argued from the code where it is not
  tested. (ruled 2026-11-08, foreman at the plan gate, PLAN deviation row 6)

## Related materials

- Seed: [`issues/truncate-recording-after-ancestor-rename.md`](../../issues/truncate-recording-after-ancestor-rename.md)
- Origin: `spec/source-path-truncate-and-stat/`. See its `REVIEW.md` for review cycle 2's F6, review
  cycle 3's F1, and "Geoffrey's rulings on the cycle 3 escalation" (item 5). See
  `remediation/cycle2-mutant-sweep.md` for E1, the counter guard.
- Previous cycle (shape precedent, not this contract): `spec/source-served-open-race/`.
- Related seeds: `issues/entry-identity-token.md`, `issues/freethreaded-concurrency-stress.md`,
  `issues/engine-error-mapping.md`, `issues/source-commit-staged-publish.md`.
- Contract text: `AGENTS.md` § Invariants § 1, § 5, § 7 and § 10; the high-blast-radius tables (the
  `write`/`_mark_handle_dirty`/`truncate` row, and the `_vacate` row); § Known open violations
  (`AGENTS.md:682`).
- Sources, anchors re-confirmed at `630a406`:
  - `src/sosfs/engine/overlay.py`:
    - `rename` 411, with its locks 427, `_vacate` 444, `catalog.rename` 447 and `tier.rename` 462;
    - `open` 621;
    - `_serve` 665, with `fastest_copy` 681;
    - `_lookup_again` 695;
    - `truncate` 735, with its lookup under the lock 766, `mark_dirty` 774, the cut 775-779, and the
      recording 780-785;
    - `_held_path` 844;
    - `_vacate` 1236;
  - `src/sosfs/engine/transfer.py`: `copy` 64, with its recording 105-118;
  - `src/sosfs/state/catalog.py`: `rename` 170, `update_meta` 151, `update_residency` 235;
  - `src/sosfs/state/journal.py`: `mark_dirty`'s `_entry_id` 131;
  - `src/sosfs/state/registry.py`: `adjust_usage` 168;
  - `tests/engine/test_replay.py`: `assert_converged` 190.
