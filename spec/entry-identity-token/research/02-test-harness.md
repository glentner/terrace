# Research 02: the tests, their mutants, and the baseline (R1–R4, R7)

Anchors are at `5893e1f`. The lab is `$S/c6plan-lab-02` (`test_lab02.py` for the baseline, `test_fixed.py` for the
draft tests, `make_fix.py` for a copy of `src/sosfs` with the purge watch applied and mutants switched by
`$LAB_MODE`). Nothing under `$WT` was edited.

## 1. Harness facts that shape the tests

- **Building blocks** (`tests/engine/test_replay.py`): `source_served` (:651) builds 2 tiers, with the stage at
  `bypass_size=100`, then walks. `Interleaved` (:675) runs `action` on a helper thread, joined, the first time the
  test thread enters `_source_lock`. `AfterLookup` (:1323) runs it right after the first `_ensure(path)` on the test
  thread. The other helpers are `rewrite` (:226), `tree` (:146), `assert_cut` (:665) and `assert_converged` (:194).
  `race`, `stop_at` and `Watched` (:1733–1859) are not needed here.
- **Forcing the reuse.** The rename adds a tombstone row at `old` (`overlay.py:457-459`). That row gets the next id,
  so the named file's id comes back only after `flush` purges both the file's tombstone (`:1121`) and the rename's
  own tombstone (`:1142`). The route used below:
  1. `rename('/data','/moved')`; `unlink`; `flush()`;
  2. `create('/moved/f')`, then `rewrite`; `flush()`;
  3. optionally `evict_copy(...,'scratch')`;
  4. `rename('/moved','/data')`.

  Each test asserts `getattr(path).id == old`.
- **Where the new file is served from.** `concurrent_removal(recreate=...)` (:707) leaves the new file with a
  **clean stage copy plus the source copy**, so the existing reused-id tests' new file is served off the stage.
  Making it served off the source takes an `evict_copy` after the flush.
- **A test that arms `Interleaved` must disarm it** (`engine._source_lock = lock.real`) if the fixed call may refuse
  before it takes the lock. Otherwise the test's own later `rename` fires the action on a helper thread that blocks
  on the path locks the test thread holds. This hung the lab once.
- **No `pytest-timeout`** is installed. A mutant whose naming loop never resets the watch spins forever. Run mutants
  under `$S/pgtimeout.py`.

## 2. The tests (each drains with `flush()` and ends in `assert_converged`)

| # | Sequence | Setup and injection | Assert on the fix | Baseline (measured) |
|---|---|---|---|---|
| 1 | R1, new file source-served | `data/big.bin` 1000 B. `Interleaved`: the route with a 500 B new file, evicted | ENOENT; `id == old`; `getattr` 500; residencies `{project}`; source `moved/big.bin` = `N*500`; `assert_cut(...,500)` | returns; new file cut to 4 (`project: clean 4`) |
| 2 | R1, new file stage-served | as 1, with a 50 B new file kept on the stage | ENOENT; residencies `{project: clean 50, scratch: clean 50}`; usage `(50,1)` | returns; `scratch: dirty 4`, usage `(4,1)` |
| 2b | R1, named file never committed (optional) | `create` + `rewrite('/data/s.txt', 5 B)`. The unlink purges at once via `_remove` (`:534`), then flush, create 50 B, carry | ENOENT; new file 50 B | returns (cut) |
| 3 | R2 | as 1, with `open('/data/big.bin')` | ENOENT; `engine._handles == {}`; source bytes unchanged | handle on `project`, reads `N*500` |
| 4 | R3 | `data/s.txt` 5 B, never opened. The route with a 50 B new file, evicted; `Interleaved` fires at the promotion's lock | ENOENT; `journal.pending()==[]`; no `scratch` residency; no `scratch/data/s.txt`; usage `(0,0)`; `_handles=={}` | handle on `scratch` reading `N*50`; stage copy `clean 50`, usage `(50,1)` |
| 5–7 | R4 flush × cut, source-served open, promoting open | before the call, `unlink('/other.bin')` (a tombstone with a queued unlink). `Interleaved`: `flush()`, then assert that the tombstone is purged | cut: `assert_cut(x*1000, 4)`; open: `tier=='project'`, reads `x*1000`; promote: `tier=='scratch'`, reads `small` | the same (correct) |
| 8–10 | R4 create × the three calls | before the call, `create('/third.bin')`, `rewrite`, `flush`, `unlink`: a tombstone holding the **max id**. `Interleaved`: `create('/third.bin')`. Its `_vacate` replays the unlink, which purges, and the create **takes the purged id** (asserted) | as 5–7 | the same (correct) |
| 11 | loop (a) | `BeforeLookup`, a wrapper on `engine._ensure` that runs the whole route (evicted) before the real call | returns; `id == old`; `assert_cut(N*500, 4)`: the call acts on the file it found | returns (the same) |
| 12 | loop (b), cut only | `AfterLookup` runs route steps 1–3 (the purge and the reuse, no carry). `Interleaved` runs the carry `rename('/moved','/data')` | ENOENT. The re-look finds nothing at `/data/big.bin`, so the call **refuses in the naming loop and never takes the lock** (`Interleaved.ran` False). Disarm, carry by hand, flush, converge | returns; new file cut to 4 |

Every reuse in the table was confirmed (`id at path == old id`). The R4-create tests confirmed `created id == third id
== max id`.

Loop (b) on `open` is not usable. On the baseline, `_serve` raises `EngineError` (no resident copy, `overlay.py:690`),
which is the GOAL's non-goal. On the fix, the naming loop's ENOENT comes first. Keep loop (b) on the cut, whose
`_serve` runs under the lock.

## 3. Mutants (lab: `make_fix.py` modes, each run against the full unit suite and the 13 draft tests)

| Mode (mutant) | Unit suite (530) | Draft tests turned red |
|---|---|---|
| `fix` | 530 pass | none (13/13 pass) |
| `norecord`: `_purge` records nothing (fix removal) | 530 pass | 1, 2, 2b, 3, 4, 12 |
| `ignore`: `_lookup_again` ignores the watch | 530 pass | 1, 2, 3, 4 (12 stays green: the naming loop catches it) |
| `anypurge`: refuse when the watch is non-empty | **13 existing fail** (e.g. `:1008`, `:1109`, `:1412`, `:1430`) | 5–10 |
| `refuse_ambig`: refuse instead of looking again | 530 pass | 11 |
| `late_watch`: watch opened after the first `_ensure` | 530 pass | 12 only |
| `skip534`: `_remove`'s purge bypasses `_purge` | not run | 2b only |
| `vacate_autocommit`: `_vacate`'s purge outside a transaction (commit, then record) | 530 pass | **none** |

Each draft test therefore has a killing mutant:
- 1–4 and 12 fail when the fix is removed;
- 5–10 are green at baseline and are killed by `anypurge`;
- 11 is killed by `refuse_ambig`.

Tests 8–10 also kill an argued (not run) mutant that refuses when a watched id is live in the catalog again.

**The commit-ordering mutant is not visible to any deterministic test, and none should be claimed.** `_vacate`'s
purge (`:1267`) runs under the source lock, and every acceptance check is made under that lock, so the check cannot
tell "record, then commit" from "commit, then record". A naming `_ensure` that falls in that window cannot find the
purged id at its path: carrying a file there takes the source lock, and a `create` at the path takes the path lock
the call holds. The ordering matters only for `_remove`'s purge (`:534`), which holds no source lock. Exposing it
would need a `race`-style three-thread driver, plus a seam between commit and record that the fixed code does not
have. Grade it by reading (R6).

## 4. Findings that bear on the plan

- **Remove a watch by identity.** In my first lab build the watches were bare `set`s, removed with
  `list.remove`. Two open empty watches (the test thread's, plus a nested `open` inside `rewrite` on the helper)
  compare equal, so the wrong one was removed, and 4 existing tests raised `ValueError`
  (`:1008`, `:1043`, `:1151`, `:1583`).
- **`_serve` needs the watch.** The cut calls `_serve` under the lock (`:784`), and so does `open` (`:656`); the
  promotion's `_lookup_again` (`:696`) must check the same watch.
- **`:1979-1990` drifts.** The test there pauses at the first `_lookup_again` on the cut thread
  (`stop_at(e, '_lookup_again', pause)`). Folding the cut's inline check (`:781-783`) into `_lookup_again` moves that
  pause from the promotion's lookup to the cut's own lookup under the lock. It still passes (530/530), but its
  docstring ("where the promotion looks the entry up again") would no longer describe what it pins. Say so in the
  plan, or keep the inline check out of `_lookup_again`'s name.
- **The count.** 10 R-sequence tests, plus 2 loop tests, plus the optional 2b, take the suite from 530 to 542
  (543 with 2b).

## 5. Readings for the gate

- **R2, "no handle registered for the new file's entry."** `_handles` is keyed by entry id, and the new file has
  the old id, so `engine._handles == {}` (or `old not in engine._handles`) is the observable. `release` pops empty
  lists (`overlay.py:823`).
- **R3, "bytes or a residency … that the call made."** This is testable only if the new file starts with no stage
  copy, hence the evict in route step 3. Without it, the new file's own stage copy cannot be told from one the call
  made.
- **R4, "its outcome SHALL be the one it has when none of that happens."** Assert the call's own result: the cut's
  size and bytes, and the handle's tier and bytes. The create adds a stage file and a counter row, so whole-state
  equality does not hold.
- **R1, "served off a stage."** The new file is the staged one, which is what the test does. The serving copy is
  decided under the lock for the cut. For `open`, the serving tier is decided before the wait (`:656`), so R2 holds
  whatever copies the new file has. Baseline R2 with a staged new file still opened `project`.
- **R7, "each converges."** Test 12 converges only after the test disarms `Interleaved` and carries the file
  itself, because the fixed call never reaches the wait. It is the planner's own decision test, not an R1–R4
  sequence.
- **Purge sites.** No test makes `_vacate`'s purge (`:1267`) the named entry's purge. The `_purge` helper covers it
  by construction; if the plan wants a mutant for it, none is available.
