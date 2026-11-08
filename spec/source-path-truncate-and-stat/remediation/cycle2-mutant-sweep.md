# Review cycle 2 remediation: the mutant sweep

The sweep review cycle 2 routed under F3, re-run on the restructured handle-less `truncate` (2026-11-07). It
covers every decision in the no-handle branch and in `_held_path`. Gate mutant ids (`M1`–`M29`, `E1`, `E2`)
are this cycle's series in `verify/truncate_gate.py`. The review's ids (its `M01`–`M20`, `H1`–`H3`) are a
separate series, mapped at the end.

## How it ran

- **Targeted pass:** `truncate_gate.py mutants` plants each `MUTANTS` entry into a `git archive` copy of the
  committed `src/`, runs `tests/engine/test_replay.py -m unit` with that copy first on `PYTHONPATH` (the run
  checks which `sosfs` it imported), and requires every test the entry names to fail while the rest of the
  file still runs (tolerant collection: a mutant that breaks import is red, not a survivor). It ran on the
  final tests in a throwaway worktree holding the work as one commit, and again on the cycle branch after
  the commit (TECH P1 remediation, review cycle 2, records the runs).
- **Full pass:** sweep-only mutants and the two equivalents ran on the whole suite, `CI=true uv run pytest
  -n auto -m unit`, each from its own scratch copy of `src/`. The mutant copies, the sweep script and the
  logs stay in the worktree's ignored `.local/rem2/`; none is committed.

## Results

Killed, by the exact assertion in the tests the gate names (each fails, the rest of the file runs):

| Mutant | Decision | Killed by |
|---|---|---|
| M1 | the cut opens the logical path | the seven R1 and swap tests |
| M2 | no source lock | cuts-under-lock, resolves-inside-lock, the five interleaved tests of cycle 1, staged-holds-lock, flip, wrong-object, symlink-takes-id, directory-takes-id |
| M3 | serving copy and held place from the entry read before the lock | resolves-inside-lock |
| M4 | the lock only when the copy served before the wait is the source (the reviewed shape) | staged-holds-lock, flip, wrong-object |
| M5 | `_held_path`'s tier inverted | staged control, own rename |
| M6 | the entry looked up by id alone | the three concurrent user-op tests |
| M7 | `_stat_held` at the logical path | both bypass-stage size tests, the earlier size test |
| M8 | `_record` without the source lock | the `_record` lock pin |
| M9 | the source lock never released | cuts-under-lock |
| M10 | the source counter adjusted | own rename |
| M11 | the dirty mark after the cut (§ 1) | staged control |
| M12 | the serving copy decided before the wait | faster-copy, flip |
| M13 | no id comparison under the lock | carried-onto-path, wrong-object |
| M14 | a missing entry under the lock not refused | concurrent removal, reused id |
| M15 | a path with no entry not refused | missing-path-or-directory |
| M16 | the kind check before the lock dropped | missing-path-or-directory, symlink |
| M17 | no promotion (`fastest_copy` in place of `_serve`) | staged control, flip |
| M18 | nothing marked dirty | staged control, faster-copy, flip |
| M19 | no size recorded in the entry | the seven R1 and swap tests |
| M20 | no size recorded in the residency | own rename, renamed directory, staged control |
| M21 | the kind not decided under the lock | symlink-takes-id (`ELOOP`, not `ENOENT`), directory-takes-id (`EngineError`) |
| M22 | the open follows a symbolic link | never-follows-a-link |
| M23 | only a directory refused before the lock (the review's M03) | symlink |
| M24 | no counter moved | staged control, faster-copy, flip |
| M25 | a staged copy cut at the source path | staged control |
| M26 | every tier holds the bytes at the source path | staged control |
| M27 | no path lock | staged-holds-lock |
| M28 | the entry not read again under the lock | resolves-inside-lock, the three concurrent user-op tests |
| M29 | `_held_path` always the logical path | the seven R1 and swap tests, own-rename size test |
| S1 | `catalog.get` in place of `_ensure` before the lock (full pass only) | `tests/fuse/test_adapter.py::test_truncate_by_path` (`FileNotFoundError: '/top.txt'`); 499 others passed |

Survivors: none.

Equivalent, both run on the full pass and passing it (500 passed each):

- **E1** drops the counter guard's `previous is not None`. Re-judged after the restructure, as the review asked.
  Under the path lock and the source lock, the serving copy was just found or made by `_serve`, so its
  residency row exists while the entry stays at the path; evicting it needs the path lock, and a dirty stage
  copy is never evicted. The two differ only where a rename of an ancestor, already past its `_vacate` when
  the cut takes the source lock, moves the entry before the recording. That is review cycle 2's F6, ruled out
  of this cycle and recorded in `issues/truncate-recording-after-ancestor-rename.md` and § *Known open
  violations*. There this tree leaves the size unrecorded, and E1 raises `AttributeError` after the bytes are
  cut (a probe that runs the rename in the cut's own thread at the stage `open_direct` return: `AttributeError
  'NoneType' object has no attribute 'size'`, then `getattr` 5). Not strictly equivalent; it differs only inside
  a recorded open violation, where the guard keeps the call from raising a non-`OSError`. The guard stays and
  is not pinned: a pin would assert the stale size the seed exists to fix.
- **E2** drops `_held_path`'s `source_path is not None`. Unchanged from cycle 1: every copy on the source sets
  `source_path` in the transaction that records it (adoption, `create` onto the source, a commit's confirm, the
  replay), so a source residency never meets a NULL `source_path`.

## The review's survivors and ids

- **Review M20** (the counter guard back to the `on_source` decided before the wait) has no counterpart: no
  value is decided before the wait any more. Its pin is `test_truncate_without_a_handle_cuts_a_faster_copy_made_during_the_wait`,
  which now asserts the stage counters (4 bytes and 1 file after the cut, 0 and 0 after eviction). Its teeth,
  shown on the pre-pin engine (a detached worktree at `9d5b6a7`, the mutant planted there): the test as it was
  passes (`1 passed`), and the test with the counter assertions fails, `assert (10, 1) == (4, 1)`.
- **Review M03** (`kind != 'file'` to `kind == 'dir'` before the lock) is gate M23, killed by
  `test_truncate_without_a_handle_of_a_symlink`. On the pre-pin engine with the mutant planted, the old
  `test_replay.py` passed whole (48 passed) and the new test fails, `DID NOT RAISE IsADirectoryError`. On this
  tree the kind decided under the lock would still refuse the link, as `ENOENT`; the test asserts `EISDIR`.
- **Review M05** (always take the source lock) is the code now. **Review M08** (drop the id comparison) is gate
  M13. **Review M18 = E1** and **H2 = E2**, above.
