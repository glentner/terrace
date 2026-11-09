# Research 02: the test harness for R1, R2, R3 and R6

Anchors are at `d6d2280` (code unchanged since `630a406`). Lab runs used a scratch copy of the setup, a
venv outside `$WT`, and the fix applied by monkeypatch. No repo file was edited.

## 1. How `tests/engine/test_replay.py` is organized

- **Fixtures** (`tests/engine/conftest.py`). `Clock` (:23) ticks 1.0 per call from 100. `seed` (:35)
  puts a file straight onto a tier root. `build_spec` (:45) builds project (the source) plus up to three
  stages (scratch, tmp, shm), with per-tier quotas through `<name>_quota=`. The `engine` fixture (:71) is
  a started 2-tier stack.
- **Module.** `pytestmark = mark.unit` (:80). The module has no `parametrize`. Every test is a named
  function with a docstring and an inline `#` comment on its key lines.
- **Helpers.**
  - `tree` (:142) reads a tier root. `view`, `sizes` and `walk` are at :153, :170 and :183.
  - `assert_converged` (:190) does these in order:
    - the queue is drained and `journal.pending() == []`;
    - no directory is unlisted;
    - live catalog paths == the source tree == the overlay view;
    - every `getattr` and residency size equals the byte count;
    - it stops the engine and checks that a fresh engine reads the same view.

    It does not check the stage counters.
  - `rewrite` is at :222.
- **The cut's section** runs from :645 to :1315.
  - `source_served` (:647) seeds the files and builds a stage with `bypass_size=100`, then starts and
    walks the engine. A 5-byte file is promoted by the cut itself. A 1000-byte file stays on the source.
  - `assert_cut` (:661) checks that `getattr == length` and that a fresh read returns the original
    bytes cut to `length`.
- **The injection techniques:**
  1. **One-shot monkeypatch of a tier method on the instance.** The wrapper `delattr`s itself, and
     the test `del`s it afterwards. `open_direct` is wrapped at :840 and :919. `open_direct` together
     with `lock_probe` is at :1084 and :1120. `open_read` is at :1503 and :1713.
  2. **`Interleaved`** (:671) stands in for `engine._source_lock`. The first time the caller enters it,
     it runs `action` on a **real helper thread to completion (`join()`, no timeout)** before taking
     the real lock. It forwards only `__enter__` and `__exit__`, and it re-raises the action's error.
     Used at :864–1283 (truncate) and :1522–1629 (open).
  3. **`AfterLookup`** (:1319) wraps `engine._ensure` for one path and runs the action on a helper
     thread right after the lookup. Used for open, at :1371–1456.
  4. **`lock_probe`** (:716) tries `acquire(blocking=False)` on the lock from another thread and
     records the result. The test at :1685 calls `engine._source_lock._is_owned()`.

  So threads are already used, but only as **run-to-completion helpers** (`join()` with no timeout). No
  existing test pauses two threads against each other. Nothing in the module wraps `catalog.get`.
- **How tests assert counters and intents.**
  - Counters: `dataclasses.astuple(engine.registry.usage('scratch')) == (bytes, files)`, at :1028,
    :1139, :1305 and :1313.
  - Residencies: `{r.tier: (r.state, r.size) for r in engine.catalog.residencies(p)}`.
  - Intents: `engine.journal.pending() == []`, at :1419, :1434 and :1546, and inside
    `assert_converged`.
  - No test compares a counter with the residency rows. The new R2 assert needs that comparison, as a
    helper: `SELECT COALESCE(SUM(size),0), COUNT(*) FROM residency WHERE tier = {name}` (t-string) per
    stage, compared with `registry.usage(name)` and checked `>= 0`.

## 2. The lab: today's code against a held-off `rename`

**The fix as tested.** In `rename` (overlay.py:444-454), `_vacate` and the catalog-move transaction were
wrapped in one `with self._source_lock:` ("narrow"). A "wide" variant also covered the stage `tier.rename`
loop (:455-466).

**The driver** has two threads:
- **B**, the rename, runs on a thread named `renamer`. `engine._vacate` is wrapped so that B, once
  `_vacate` returns, sets `past` and waits on `go_b`.
- **C**, the cut, runs on a thread named `cutter`. The pause point is wrapped (before or after the
  call, cutter thread only) to set `at_x` and wait on `go_c`.
- **`engine._source_lock`** is replaced by a `ProbeLock` that forwards `acquire`, `release`,
  `__enter__`, `__exit__` and `_is_owned`. When the watched thread (C) makes a blocking acquire it does
  not own, it first tries `acquire(False)`. If that fails, it sets `blocked` before it blocks.

The driver runs in this order:
1. Start B and wait for `past`.
2. Start C and wait for one shared event, set by `blocked`, by `at_x`, or by C's return.
3. Set `go_b` and join B with a timeout.
4. Set `go_c` and join C with a timeout.
5. Restore the patches.

Every wait and every join has a 5 s timeout.

| Sequence (stack; C's pause point) | Today (fix removed) | Held off (narrow and wide) |
|---|---|---|
| **Same thread** F6: `data/s.txt` 5 B, cut to 2; rename inside C after stage `open_direct` | returns; getattr 5; `{'project': ('clean',5), 'scratch': ('dirty',5)}`; counter (5,1) = rows; flush, then **not converged** | **still the same** (the RLock re-enters, so the injection is unusable as a test) |
| R1 staged: two threads; after stage `open_direct` returns | identical to the same-thread row (B really runs on another thread) | C **blocked**; `ENOENT`; nothing cut; no stage copy; getattr `/moved/s.txt` 5; converged |
| R1 source-served: `data/big.bin` 1000 B; after source `open_direct` | returns; getattr 1000 while the source holds 2 B; no counter move; **not converged** | blocked; `ENOENT`; source still 1000 B; converged |
| **R2**: staged; **after the cut-thread `engine.catalog.get_residency` returns** (`:780`, before the transaction) | counter **(2,1)** against rows (5,1); flush (2,1); evict **(-3,0)**; open+release **(-1,1)** against rows (2,1); evict again (-3,0); not converged. The seed's F1 numbers exactly | blocked; `ENOENT`; counters (0,0) = rows throughout; converged |
| R3: before cut-thread `engine.catalog.fastest_copy` | **`EngineError`** "No resident copy of /data/s.txt" | blocked; `ENOENT`; converged |
| R3: promotion, **after stage `put` returns** | **`LookupError`** at `transfer.py:108` (`add_residency`); `promote` intent left in `pending()`; stage holds `moved/s.txt` 5 B with **no residency**; not converged | blocked; `ENOENT`; converged |
| R3: promotion, before source `open_read` | `FileNotFoundError` (put's rename into the moved stage dir), intent rolled back, converged: **green today**, so it cannot be pinned | blocked; `ENOENT` |
| R3: before cut-thread `engine.journal.mark_dirty` | **`LookupError`** (`journal.py:131`); stage holds a clean 5 B copy, counted; converges after a flush | blocked; `ENOENT`; converged |
| Cut first: C paused after `open_direct`, then B started; B blocks in `_vacate` | returns; getattr 2; `('dirty',2)`; counter (2,1); converged (stage and source-served; the seed's "argued, not run" claim now runs) | same |

Notes on the table:
- **The tombstone.** The rename's tombstone is only at `/data`, so after the move nothing is at
  `/data/s.txt`. The recording's `update_meta` and `update_residency` match no row.
- **Why the R2 pause sits after `get_residency`.** At `open_direct` the counter does not drift, because
  `previous` is read after the move: it is `None` and the guard skips. The counter only drifts when the
  move lands between `:780` and `:781`.
- **Determinism.** The test was run 50 times for each of four points (stage `open_direct`,
  `get_residency`, source `open_direct`, after `put`) and for each of the current code, narrow and wide.
  Every run gave the same outcome: `at-point` today and `blocked` under the fix. Each 50 runs took about
  0.3 s.
- **R6.** With the narrow or the wide fix patched into every xdist worker (`-p` plugin), the full
  `uv run pytest -n auto -m unit` run reported **521 passed**, and `test_replay.py` passed **76**.
  The `Interleaved` and `AfterLookup` actions run to completion before the caller takes the lock, so
  the wider hold never meets them.

## 3. Proposed tests

They go in the cut's section, after :1313, under `-m unit`. Put two helpers there:
- `past_vacate_then_cut(engine, path, length, owner, name, when)`: the driver above, returning
  `(outcome, cut_error, rename_error)`;
- `assert_counters_agree(engine)`.

Each test makes these assertions:
- **The R1 disjunction, not the probe's outcome.** Either the call raised `FileNotFoundError` and nothing
  was cut: `tree(project)` and `tree(scratch)` hold the original bytes, or no copy at all, and
  `getattr('/moved/…').size` is the original size. Or the call returned and `assert_cut(engine,
  '/moved/…', original, length)` passes, with the cut copy's residency size equal to `length`.
- **R2:** `assert_counters_agree` at each step.
- **R3:** the error, if any, `isinstance(…, OSError)`; `engine.journal.pending() == []`; no stage file
  without a residency.
- `engine.flush()`, then `assert_converged`.

Every join has `timeout=5`, followed by `assert not t.is_alive()`, so a deadlock fails the test instead
of hanging it. The driver also asserts `rename_error is None`.

| # | Name | B | C pauses | Under the held-off fix | Fix-removal mutant (rename's lock back to `_vacate` only) |
|---|---|---|---|---|---|
| 1 | `test_truncate_without_a_handle_of_a_staged_copy_while_an_ancestor_rename_is_past_its_vacate` | `/data` to `/moved` | after scratch `open_direct` | ENOENT, nothing cut, converged | returns; getattr 5 ≠ 2; residency dirty 5 |
| 2 | `test_truncate_without_a_handle_of_a_source_served_file_while_an_ancestor_rename_is_past_its_vacate` | same | after source `open_direct` (`big.bin` 1000 B) | same | returns; getattr 1000 ≠ 2; not converged |
| 3 | `test_truncate_without_a_handle_keeps_the_stage_counter_while_an_ancestor_rename_is_past_its_vacate` (R2) | same | after cut-thread `engine.catalog.get_residency` | ENOENT; counters agree on return, after flush, after open+release and evict of `/moved/s.txt`; none negative | counter (2,1) against rows (5,1) |
| 4 | `test_truncate_without_a_handle_serves_no_engine_error_while_an_ancestor_rename_is_past_its_vacate` | same | before cut-thread `engine.catalog.fastest_copy` | ENOENT | `EngineError` |
| 5 | `test_truncate_without_a_handle_leaks_no_promotion_while_an_ancestor_rename_is_past_its_vacate` | same | after scratch `put` returns (in `transfer.copy`) | ENOENT; `pending() == []`; scratch tree empty | `LookupError`; promote intent pending; stage bytes, no residency |
| 6 | `test_truncate_without_a_handle_marks_nothing_dirty_off_the_path_while_an_ancestor_rename_is_past_its_vacate` | same | before cut-thread `engine.journal.mark_dirty` | ENOENT | `LookupError` |

That makes six new tests: `test_replay.py` goes from 76 to 82, and the unit suite from 521 to **527**.

- **The cut-first ordering** (last row of the §2 table) is green both today and under the fix. No
  fix-removal mutant turns it red, so it does not satisfy R6. Add it only if the planner wants R1's
  "return" outcome pinned; it would then be a 7th test, with its red mutant dropping `_vacate`'s lock.
- **The same-thread injection** stays red under a held-off fix, so it must not be used.
- **A wide-vs-narrow mutant.** Under the narrow fix, a cut at the **new** path that starts between B's
  catalog commit and its stage move gets `ENOENT` from the stage open after its dirty mark. The plan-gate
  ruling accepts that, and it lies outside R1, whose rename lands after the lookup. If the plan picks
  wide, it needs its own test and mutant.
- **R5.** Under held-off, `previous` at `:780` is never `None` for a stage that serves:
  - a promotion just recorded the residency, or `fastest_copy` read it, under both locks;
  - the file's path cannot change during the cut;
  - eviction and unlink need the file's path lock.

  So the guard is unreachable, and no test can pin it under the fix. That argues for removal. The
  planner should confirm the other `drop_residency` callers (overlay.py:516, :1105) cannot fire under the
  cut's two locks. Under the mutant with the guard also dropped, test 1 raises `AttributeError`, because
  the move lands before `:780`.
- **Mutant mechanics.** One mutant, reverting the `with self._source_lock:` that wraps
  `_vacate`→transaction in `rename`, turns all six red, each with a distinct symptom. Under it the
  driver reaches `at-point`, so it never hangs.

## 4. Running and counting

From `$WT`, with the uv env vars set (`UV_PROJECT_ENVIRONMENT` pointed outside `$WT` avoids a
`.venv`), run `uv run pytest -n auto -m unit -q`. Today it reports **521 passed** (about 30–50 s).
`uv run pytest -n auto -m unit -q tests/engine/test_replay.py` reports **76 passed**, and
`--collect-only -q` collects 76. The suite has no pytest-timeout plugin, which is why the joins carry
their own timeouts.

A unit run leaves `.hypothesis/` in `$WT` (from `test_model.py`). Move it out after each run. Pass
`-p no:cacheprovider` to keep `.pytest_cache` out.
