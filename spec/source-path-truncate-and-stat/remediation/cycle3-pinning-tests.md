# Review cycle 3 remediation: pins for the two surviving mutants

Review cycle 3, F3 (2026-11-08). The review's mutant series (its `mut/results.jsonl`) is separate from the
gate's `MUTANTS` series in `verify/truncate_gate.py`: **M11** and **M27** below are the review's ids. The gate
is frozen and gains no entry; this note is the record that each new test kills its mutant.

| Review mutant | Planted change (`src/sosfs/engine/overlay.py`, `truncate`'s no-handle branch) | Pinned by |
|---|---|---|
| M11 | `serving = self._serve(current)` → `serving = self._serve(entry)` | `test_truncate_without_a_handle_promotes_from_where_a_replay_during_the_wait_moved_the_file` |
| M27 | `previous = self.catalog.get_residency(path, serving)` → `previous = self.catalog.get_residency(path, self.source.name)` | `test_truncate_without_a_handle_counts_the_stage_copy_it_cuts` |

Both tests are in `tests/engine/test_replay.py`, appended after the last test, and end in `assert_converged`.

- **M11 pin.** A 5-byte `a.txt` on the source, renamed to `s.txt` (queued), cut to 2 bytes with no handle while
  `Interleaved` runs `flush()` during the wait, so the file's own rename replays and moves the source object.
  The stage admits 5 bytes, so the cut promotes under the lock. HEAD promotes from the entry found under the
  lock and returns; the residencies are `project ('clean', 5)`, `scratch ('dirty', 2)`; the flush converges.
- **M27 pin.** A 5-byte `data/s.txt`, rewritten through a handle to 8 bytes (stage `('dirty', 8)`, source
  `('clean', 5)`, stage counter `(8, 1)`), cut to 2 with no handle, uncontended. HEAD's counter is `(2, 1)`,
  then `(0, 0)` after a flush and the copy's eviction.

## How it ran

Two throwaway worktrees at the branch's head before this run, `8517e7f`, whose `src/` and `tests/` are the
reviewed `aafdd44`'s (`git worktree add --detach
.local/rem3/m11 HEAD`, the same for `m27`), each with one mutant planted by an exact, once-only string replace
(confirmed with `git -C .local/rem3/<m> diff`: one line changed, the planted text). Each ran with its own
`src/` first on the path, from the cycle worktree's environment (the import was checked to resolve inside
the throwaway tree):

```
cd .local/rem3/<m>
PYTHONPATH=$PWD/src:$PWD $WT/.venv/bin/python -c 'import sosfs; print(sosfs.__file__)'
CI=true PYTHONPATH=$PWD/src:$PWD $WT/.venv/bin/python -m pytest -q -p no:cacheprovider -n auto -m unit
cp $WT/tests/engine/test_replay.py tests/engine/test_replay.py
PYTHONPATH=$PWD/src:$PWD $WT/.venv/bin/python -m pytest -q -p no:cacheprovider -m unit \
    tests/engine/test_replay.py -k 'promotes_from_where or counts_the_stage_copy' -rA
```

and on HEAD with the new tests, from the cycle worktree:

```
$WT/.venv/bin/python -m pytest -q -p no:cacheprovider -m unit tests/engine/test_replay.py \
    -k 'promotes_from_where or counts_the_stage_copy' -rA
```

## Results

| Tree | Suite before the pins (`CI=true -n auto -m unit`) | M11 pin | M27 pin |
|---|---|---|---|
| HEAD + pins | — | passed | passed |
| M11 | 500 passed (survives) | **failed**: `sosfs.tier.protocol.TierError: No file at /a.txt in tier project` | passed |
| M27 | 500 passed (survives) | passed | **failed**: `assert (5, 1) == (2, 1)` |

Each pin fails on its own mutant at an exact assertion (an exception on a file that exists; a counter
tuple), and passes on the other. Logs and the throwaway trees stayed in the worktree's ignored
`.local/rem3/`; the trees were removed with `git worktree remove`.
