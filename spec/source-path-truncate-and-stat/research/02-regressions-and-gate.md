# 02 — Regressions, mutants and gate (research brief)

Measured at `0a88f30`. Lab: HEAD's `src/` via `git archive` into `$TMPDIR`, plus a hand-patched fix copy
(`_held_path` helper; the source lock taken in the no-handle `truncate` when `serving == self.source.name`;
the entry re-read with `self.catalog.by_id(entry.id)` inside the lock) and one copy per mutant. Each copy goes
first on `PYTHONPATH`, and every run prints `sosfs.__file__` to show which copy it imported.

## Findings

**1. R1/R3 test design.**
- `build_spec` puts the source first: `tiers=(source,) + stages` (`tests/engine/conftest.py:60-61`).
  So `assert_converged`'s `engine.spec.tiers[0].path` (`test_replay.py:188`) is the source on any stack.
- `assert_converged` passed on the two-tier `bypass_size=100` stack in every lab case, and in the shaper's
  `probe_r2.py` at HEAD.
- The `engine` fixture (`conftest.py:70-80`) builds two tiers (project → scratch) with no `bypass_size`. It
  seeds `top.txt`, `data/a.txt` and `data/b.txt`, so nothing in it is source-served. The R1 and R2 tests
  therefore build their own stack, as `test_create_on_the_source_inside_renamed_directory` does
  (`test_replay.py:329`): `build_spec(tmp_path, tiers=2, scratch_quota=QuotaSpec(bypass_size=100))`, or
  `tiers=1`.
- R3 coverage by `assert_converged` (`test_replay.py:173-202`):
  - the queue drained (182);
  - no unlisted directory (183-185);
  - the live catalog set equals the source tree's path set (186-189). That rules out a leftover pre-rename
    path, since the tree must hold no path the catalog lacks;
  - kinds and bytes: source equals the view (190-191, `'DIR'` against bytes);
  - each getattr and residency size is in `{len(bytes)}` (192-193, via `sizes`, 153);
  - a fresh `OverlayEngine` reads the same view and sizes (194-202).

  It covers R3 fully. It does not check sizes while a handle is open; R2 does that inline.
- **Trap for R1's residency assertion.** After a shrink below `bypass_size`, the fresh-handle read promotes
  the file to scratch, so the residencies become `{'project': 4, 'scratch': 4}`. Assert
  `{tier: size} == {'project': n}` before the read, and `{r.size for r in residencies} == {n}` after it. A
  grow to 2000 stays source-only.

**2. R2.**
- The R2 probe at HEAD passes, for both the file's own rename and an ancestor's: getattr is 4 while open,
  and (getattr, source residency) is (4, 4) after fsync, release and flush. The residency stays at 1000
  while the handle is open, as GOAL says.
- The test guards `_stat_held`'s source-path resolution (`overlay.py:788`), which serves both getattr's live
  stat (290) and `_record` (769).
- Planting `where = entry.path` makes R2 go wrong in both variants: 1000 at every checkpoint, and
  `assert_converged` fails.
- **The `_record` lock is deterministically testable**, contrary to the expectation. Wrap `engine.source.stat`
  only around `engine.release(h)`. Inside the wrapper, a helper thread calls
  `engine._source_lock.acquire(blocking=False)`; it releases the lock if it got it, and records whether
  it was held.
  - At HEAD the lock is held.
  - With the mutant `with contextlib.nullcontext():` at `overlay.py:765`, the probe reports `[False]`.
  - Optional: the previous cycle closed this, and the test would be a bonus guard, not an R-item.

**3. Deterministic concurrency (both proven in the lab, with no sleeps or timeouts).**
- **(a) Lock held at open.** Patch `engine.source.open_direct` once, in the `unlink_during_next` style
  (`setattr`, then `delattr` on first call; `test_replay.py:220-238`). Inside it, a helper thread probes
  `_source_lock.acquire(blocking=False)`, then the real `open_direct` runs.
  - Assert `held == [True]`, then flush and `assert_converged`.
  - The fd follows the inode once it is open, so the lock only has to span resolve → open. `open_direct` is
    the right probe point; there is no need to patch `os.ftruncate`.
- **(b) Entry re-read inside the lock.** Set `engine._source_lock = FlushFirst(engine)`. The wrapper's first
  `__enter__` from the test thread runs `engine.flush()` to completion in a helper thread (`start`, then
  `join`). It records any exception, then delegates to the real RLock. `__exit__` delegates too, and helper
  threads always delegate.
  - Restore the lock in a `finally`.
  - Then assert: the wrapper fired, the helper raised nothing, `journal.queued() == []`, and
    `tree(root) == {'moved.bin': X[:4]}`; then `assert_converged`.
  - Against a stale pre-lock entry, the truncate raises `FileNotFoundError` on the old source path. It also
    catches an entry re-read before the lock rather than inside it.
- **Deadlocks: none.**
  - `flush` takes `_source_lock` per namespace row (`overlay.py:956`). `_execute_namespace_locked` and
    `_replay_through`/`_vacate` take no `locks.hold`.
  - `flush`'s data pass takes path locks through `commit_upstream` (874), but only for files with dirty
    rows.
  - The truncating thread holds only its own path lock. A source-served file has no upstream, so `truncate`
    queues no data row for it (`overlay.py:710-711`).
  - When the serving tier has an upstream, the fix uses `nullcontext` and the wrapper never fires.
  - The sqlite connection is `check_same_thread=False` (`state/db.py:92`).

  Use both tests. (a) is the direct lock assertion; (b) is the re-read assertion, and it also goes red when
  the lock is dropped, because the wrapper never fires.

**4. Kill matrix (lab, fix copy plus mutants).** The fix passes all 12 lab cases. Each mutant fails exactly the
expected ones:

| Mutant (planted into the fix) | Red |
|---|---|
| F1 `open_direct(_pure(self._held_path(entry, serving)), …)` → `_pure(path)` | all 7 R1/swap cases (ENOENT) and (a). (b) passes, as expected: the logical path is right after the flush |
| F2 `with self._source_lock if serving == self.source.name else contextlib.nullcontext():` → `with contextlib.nullcontext():` | (a) `[False]` and (b) not fired; R1 passes |
| F3 delete `entry = self.catalog.by_id(entry.id)` (in truncate) | (b) only (ENOENT) |
| F4 `_held_path` returns `entry.path` | R1 (ENOENT), both R2 tests, and the existing `test_size_of_a_file_created_on_the_source_inside_renamed_directory` |
| M8′ if `_stat_held` keeps its own `where =` line: that → `where = entry.path` | both R2 tests (measured at HEAD) |

- `replay_gate.py`'s `MUTANTS` (597-634) holds tuples of (label, file, exact old, new, tests, model?).
- `mode_mutants` (636-662) checks that src and tests are committed and that each anchor occurs once. It
  plants each mutant into a `git archive HEAD src` copy, checks the import, runs `test_replay.py` with JUnit,
  and requires `outcome == ['failed']` per named test.
- `on_baseline` and `regressions` (245-314) run the tests against an older engine; `failed_for_cause` rejects
  API-drift failures.
- `regression_shape` (273-289) uses the AST to check that each test walks the tree and ends in
  `assert_converged`.
- Reuse: the mutant loop, `on_baseline` and `failed_for_cause`, `regression_shape`, `junit`, `stated_unit_count`,
  `mode_seeds` and `mode_ci`.
- Drop: the model half of each mutant (no model mutant reaches a source-served file), `REVIEWED`, the
  `NO_SHRINK` baseline model run, and the `records` and `ledger` specifics. Rewrite `records` for R4.
- The previous M8 anchor (`where = entry.source_path if …`) moves if the fix introduces `_held_path`.
  Re-anchor it.

**5. Hypothesis CI draw.**
- Under `derandomize` (the `ci` profile), the seed is `Random(int_from_bytes(function_digest(test)))`
  (`hypothesis/core.py:715-716`, hypothesis 6.155.7). For a state machine, `test` is hypothesis's own
  `run_state_machine`, whose `_hypothesis_internal_add_digest = function_digest(OverlayMachine)`
  (`stateful.py:238-240`).
- That is a sha384 of the class's source, with decorators, comments and blank lines stripped
  (`reflection.py:56-80, 83-113`), plus its signature.
- Measured: `function_digest(OverlayMachine)` is identical (`fae4c153…`) for HEAD and for a tree that
  appends a test to `test_replay.py`, a fixture to `conftest.py`, and a change to `overlay.py`.
- So none of these redraw the sample. A `conftest.py` edit can still change the model's behaviour under the
  same draw, if it alters `Clock`, `seed` or `build_spec`, which `test_model.py:26` imports. Add helpers;
  don't modify those.
- The fix leaves the model's behaviour alone: the model runs three tiers with no quota (`test_model.py:91`),
  so nothing is source-served there, and `_held_path` on a stage returns the logical path with
  `nullcontext`.

**6. Bookkeeping.**
- `AGENTS.md` states 474 at four places:
  - `:70` (`# 474 tests`);
  - `:111` (`42 files, 484 tests (474 \`unit\`, 10 \`integration\`)`);
  - `:626` (`10 skipped, 474 deselected`);
  - `:667` (`474 unit tests`).
- `:626` equals the unit count (the total minus 10), yet no `stated_unit_count` regex matches it
  (`replay_gate.py:665-671`: `(\d+) unit tests`, ``\((\d+) `unit` ``, `pytest -m unit\s+# (\d+) tests`). Bump
  it by hand, or add the regex `(\d+) deselected`.
- `42 files` counts every `.py` under `tests/`, `__init__` included, so new tests in `test_replay.py` leave it
  unchanged.
- **Coincidence:** 474 + 10 new tests = 484, the old total. Grep-based checks must not confuse the two.
- HEAD collects `474/484 tests collected (10 deselected)`.
- CI jobs (`.github/workflows/ci.yml`): `Unit (Linux, ${{ matrix.python }})` (:13, i.e. 3.15 and 3.15t),
  `Unit (macOS, engine-only)` (:54) and `Lint & build` (:68). All run `uv run pytest -n auto -m unit -rfEs`.
- macOS skips by marker, not by deselection. `tests/fuse/conftest.py:23-41` prepends
  `skipif(True, reason='libfuse cannot be loaded: …')` to every item under `tests/fuse` when importing
  `mfusepy` raises `OSError`. `tests/fuse` has 44 unit tests, which matches `FUSE_UNIT_FLOOR = 44`
  (`replay_gate.py:782`).
- `mode_ci` requires macOS passed + skipped == the Linux passed count, with every skip under `tests/fuse`
  naming libfuse.

## Recommended tests (`tests/engine/test_replay.py`, a new section, walk first, end in `assert_converged`)

Shared helpers: `bypassed(tmp_path, clock, *seeds)` builds and starts a two-tier stack with
`bypass_size=100` and walks it. `truncated_by_path(engine, path, length, original)` asserts:
- getattr equals `length`;
- the residencies are `{'project': length}`;
- a fresh handle reads `original[:length] + b'\0' * pad`;
- every residency size equals `length`.

Use a 1000-byte patterned body (`b'0123456789' * 100`).

- `test_truncate_by_path_of_a_bypassed_file_renamed_shrinking`: own rename, `truncate(..., 4)`; helper; flush.
- `test_truncate_by_path_of_a_bypassed_file_renamed_growing`: same at 2000, zero-padded; source-only after
  the read.
- `test_truncate_by_path_of_a_bypassed_file_inside_renamed_directory`: `/data → /moved`, 4 bytes.
- `test_truncate_by_path_of_an_adopted_file_inside_renamed_directory`: one tier, `/moved/a.txt` to 0 (or 2).
- `test_truncate_by_path_of_a_file_created_on_the_source_inside_renamed_directory`: one tier, create plus
  `GAMMA`, release, truncate to 2.
- `test_truncate_by_path_of_a_committed_file_renamed_on_one_tier`: the 6th Problem row: create, write, flush,
  rename, truncate to 1.
- `test_truncate_by_path_during_a_queued_swap`: `a→t`, `b→a`, `truncate('/a.bin', 4)`. Then
  `tree(root) == {'b.bin': b'bbbb', 't.bin': A}`, and `t` reads A untouched.
- `test_size_of_a_bypassed_file_truncated_through_a_handle_while_renamed`: R2, own rename: open RDWR,
  handle-truncate to 0, write `tiny`. getattr is 4 while open; (getattr, source residency) is (4, 4) after
  fsync, release and flush.
- `test_size_of_a_bypassed_file_truncated_through_a_handle_inside_renamed_directory`: R2, ancestor.
- `test_truncate_by_path_opens_the_source_under_its_lock`: probe (a).
- `test_truncate_by_path_resolves_the_entry_inside_the_source_lock`: probe (b), `FlushFirst`.
- *(optional)* `test_release_stats_a_source_held_copy_under_its_lock`: the `_record` lock probe.

The lab passed all 12 shapes on the patched fix. At HEAD the 7 R1/swap shapes and both probes raise
`FileNotFoundError`, which pytest records as failed, not error. R2 and the optional test pass at HEAD.

## Recommended mutants

F1 (logical path in truncate), F2 (lock dropped), F3 (no re-read), F4 (`_held_path` → logical), and the `_record`
lock mutant if the optional test lands. Anchor each one on the built text. Each must turn red exactly the tests
in the table.

## Gate notes

- Baseline-fail checks apply to R1/swap and (a)/(b) only. The R2 tests pass at the base by design, so their
  proof is F4, not the baseline.
- Unit floor: `> 474`. The stated count must equal passed + skipped, including `:626`.
- Seeds 1-20 and CI as in the previous gate. `FUSE_UNIT_FLOOR` stays 44.
- No model mode is needed, since `test_model.py` is unchanged. Optionally assert it is byte-identical to the
  base.
