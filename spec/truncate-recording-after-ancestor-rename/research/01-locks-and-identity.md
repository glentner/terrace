# Research 01 — locks and identity: does "held off" close R1-R4?

Read-only, at `d6d2280` (`git diff 630a406 HEAD -- src tests` is empty, so every GOAL anchor holds).
Files: `src/sosfs/engine/overlay.py` (O), `src/sosfs/state/catalog.py` (C), `state/journal.py` (J),
`engine/transfer.py` (T), `tests/engine/test_replay.py` (R).

**Verdict: held-off holds.** If `rename` wraps `_vacate` and its catalog-move transaction (O:444-454) in one
`with self._source_lock:`, then for any other thread the window from the cut's lookup under the lock
(O:766) to its recording commit (O:781-785) contains no change to any `entry.path`. Every path-keyed
read and write in the cut reaches the entry the lookup returned (R4). A stage residency cannot vanish
inside that window, so R5's guard becomes unreachable. The design breaks no clause of § 10.

## 1. Who changes `entry.path`

- **One writer.** `Catalog.rename` (C:170-182, both `UPDATE entry SET path`) is the only statement that
  rewrites `path`. Its only caller is `OverlayEngine.rename` (O:447). Its only caller in turn is
  `fuse/adapter.py:163`. No other `UPDATE entry` touches `path`: `update_meta` (C:151) whitelists
  `META_FIELDS`, which excludes `path`, and `set_listed` and `mark_deleted` change no path.
- **Add and purge cannot swap the row at the path.** `entry.path` is `UNIQUE` (`state/schema.sql:60`),
  so while the live file sits at P no `catalog.add` at P succeeds (`_adopt` O:240, `mkdir` O:331,
  `create` O:368, the rename's tombstone O:452). The purges by path are these:
  - `_remove`, O:529, under P's path lock;
  - `_vacate`, O:1252, and the replay's unlink and rmdir, O:1106. Both require a tombstone at the path.
    Because the path is UNIQUE, no tombstone can sit at P while the live file does. Both also hold the
    source lock;
  - the replay's `purge(old)`, O:1127, which requires `tomb.deleted`.
- **Result under held-off.** The cut holds P's path lock and the source lock continuously from O:765
  to O:785. `_serve`'s inner `with` (O:688) is re-entrant and releases nothing. So the file's path is
  constant and the R4 property holds. The cut's helpers all key by P: `fastest_copy` O:681,
  `_lookup_again` O:705, `transfer.copy`'s `get` and recording T:83/92/105-118, `journal.begin`'s
  `_entry_id` (J:271), `mark_dirty` J:131, `get_residency` O:780, `update_meta` and `update_residency`
  O:782-783. Each one reaches the looked-up entry.

## 2. Who drops a residency: is R5's `previous is None` reachable?

- **Every drop is in O.** `drop_residency` callers: `_remove` O:516 (stage), the replay O:1105 (source
  only), and `evict_copy` O:1287. Cascades come only from `purge` of the entry's own row; the residency
  FK is on `entry.id`, and nothing cascades from a parent. `evict.py` reaches drops only through
  `evict_copy` and `commit_upstream` (evict.py:129, :151-152). `rescue.py`, `drain.py` and
  `writeback.py` drop nothing directly: they go through `commit_to_shared`, `commit_row` or
  `commit_upstream`, which only flip `state`.
- **None can drop the cut's stage row inside the window.**
  - `_remove` of the file needs P's lock. It also runs as a rename's target (O:443) under the lock of
    `new` = P.
  - `evict_copy(Q)` drops by Q. The file is at P for the whole window, so Q ≠ P misses and Q = P
    blocks on the path lock.
  - The replay drops only source rows.
- **R5: remove the `previous is not None` half of the guard** and keep `serving != self.source.name`.
  At HEAD the only routes to `None` are an ancestor's move (this defect) and a removal after that move
  (the Non-goal). Held-off closes both: a removal would need P's lock. Removing it is safer than a pin:
  if the impossible happens, `previous.size` raises loudly instead of skipping the counter silently.
  `_record`'s twin guard (O:840) stays as it is: its stage branch is still exposed (§ 7 below).

## 3. Lock order with the widened hold (§ 10)

- **Holders of `_source_lock` today:**
  - `create` onto the source, O:356;
  - `_purge_source`, O:539;
  - `open` off the source, O:653;
  - `_serve`'s promotion off the source, O:688;
  - the handle-less `truncate`, O:765;
  - `_record` off the source, O:827;
  - `_commit_hop` to the source, O:901 (the copy only, O:901-905);
  - `_execute_namespace`, O:1021;
  - `_vacate`, O:1243.

  Re-entrant nests: `_replay_through` → `_execute_namespace`, `_purge_source` → `_replay_through`, and
  `truncate` → `_serve`.
- **The widened rename keeps the order.** It runs path locks (O:427) → source lock → `_vacate`
  (re-entry) → `db.transaction()` (O:445, which is `BEGIN IMMEDIATE`). The block contains `_vacate`'s
  replays and four DB-only calls: `catalog.rename`, `journal.queue`, `catalog.add`, `mark_deleted`. It
  calls no `locks.hold`, `_ensure`, `getattr` or `readdir`, and takes no `_handles_mutex`.
  `getattr(old)`, `_populate_tree`, `_ensure` and `_require_parent` (O:419-437) all run before it.
  `_remove(target, replaced=True)` (O:443) stays outside, before it; its `_purge_source` takes the lock
  on its own. `materialize_dirs` and `tier.rename` (O:456-466) stay after it. No new
  mutex-inside-transaction edge appears. The only cost is the catalog transaction, now under the lock.
- **The `Interleaved` test helper still fires at the same moment.** It lives at R:671 and arms on the
  caller thread's first entry to the lock. Rename's first entry was `_vacate`'s `with`, the
  first statement of `_vacate`; the widened `with` sits immediately before it. So existing tests that
  stand `Interleaved` in for the lock (R:876, :1004, …) see the same interleaving.

## 4. Does held-off close R1 and R3's windows?

- **Another thread: yes, all of them.** R1 and R3 run from the lookup (O:766) to the return. They
  cover `fastest_copy` (O:681), a promotion including its copy and `transfer.copy`'s recording by path
  (T:105-118), `mark_dirty` (O:774), the open (O:775) and the recording (O:780-785). All of it sits under
  the one held source lock. A rename on another thread is in one of three states:
  - it has not reached its widened `with`: it waits until the cut returns, then moves a correctly
    recorded file;
  - it is inside the `with`: the cut waits at O:765, then its lookup misses and it raises `ENOENT`
    before any dirty mark, promotion or cut;
  - it has committed: the lookup misses and the cut raises `ENOENT`.

  So no landing falls inside the window, and R1 and R3 hold. R1's admitted `ENOENT` outcome is the one
  that applies to "a rename already past `_vacate`".
- **A same-thread re-entrant rename (the seed's F6 injection) still lands and still breaks the
  recording**, because the lock is an `RLock`. It is not reachable in production. Nothing inside the cut
  (`_serve`, `transfer.copy`, `open_direct`, `journal`, `catalog`, `registry`) calls `rename`, and the
  FUSE adapter runs one op per request thread. The plan should say this, and its tests must model
  "past `_vacate`" with a second thread, not by re-entry.
- **A deterministic test shape that is red on the mutant.**
  1. Helper H runs `rename('/data', '/moved')`, paused by a wrapped `catalog.rename` on an `Event`. With
     the fix, H then holds the source lock; without it, H holds no lock.
  2. Thread C runs the cut through a lock wrapper that tries a non-blocking acquire first. If the
     acquire fails (the fixed tree), the wrapper releases H and then blocks.
  3. An injection at the R1 or R3 point under test (`fastest_copy`, the stage `put`, `mark_dirty`,
     `open_direct`) releases H and joins it. That point is reached only on the mutant.

  The fixed tree gives `ENOENT`, nothing cut, a converged tree, and counters that agree. The mutant
  gives F6's stale size, `EngineError` or `LookupError` by injection point. The `lock_probe` pattern at
  R:716 already exists to build on.

## 5. Residual windows held-off leaves (all outside R1 and R3: the rename lands before the lookup)

- **(a) The rename's stage-move lag.** `tier.rename` (O:462) runs after the lock is released. A cut at
  the new path between the catalog move and the stage move has three outcomes:
  - **The bytes have not moved yet.** The stage `open_direct` raises `ENOENT` after `mark_dirty`
    committed: no bytes are cut, and the stray dirty mark is harmless.
  - **The cut promotes onto a tier T whose old directory exists.** `materialize_dirs` creates T:/moved
    and the cut puts the file there. The rename's `os.replace(T:/data, T:/moved)`
    (`tier/posix.py:205`) then fails with `ENOTEMPTY`. That is not `FileNotFoundError`, so it escapes
    `rename` after the catalog committed and leaves T's other files at /data, where the catalog does not
    know them.
  - **The same path from `open`.** `open` can reach that failure too, and so can an ancestor's round
    trip whose second leg is still mid-move.

  The "Path locks do not cover descendants" bullet covers the first outcome ("reads a moved staging
  file"), but not the `ENOTEMPTY` collision. That needs the Q3 dated line. The plan could close (a) for
  the cut by moving the stage-move loop inside the hold. That costs O(1) POSIX renames under the mutex;
  it does not close `open`'s stage-served path.
- **(b) The id-reuse gap before the lookup.** Unchanged; owned by `issues/entry-identity-token.md`.
- **Side effect worth recording (R7).** Held-off also closes the concurrency issue's bullet "A replay
  racing a user rename of the entry's directory". `_execute_namespace_locked` reads `entry.path` and
  calls `journal.begin(path=…)` (O:1044, :1051) under the source lock, so the catalog move can no longer
  land between the two. It also covers `_record`'s source-served branch (O:827-841). The issue file
  and § 10 must say so, or the records under-claim.

## 6. The keyed-by-identity alternative

- **Its failure is concrete.** The cut promotes off the source onto T, under the source lock but not
  serialized against the rename's stage loop (O:456-466), which takes no source lock:
  - If the rename's `T.stat('/data')` runs before `materialize_dirs` creates T:/data (T:/data did not
    exist), the rename skips T. The put lands at T:/data/s.txt, while the residency (recorded by id)
    belongs to an entry now at /moved/s.txt. The bytes then sit at a path the catalog does not know:
    `open` on T raises `ENOENT`, and `evict_copy` deletes the wrong path, drops the row and decrements
    the counter while the orphan stays on disk.
  - If the stage directory moves mid-put, the put's `os.replace` raises `FileNotFoundError`. The intent
    rolls back; that is an `OSError`, which is acceptable.

  The planner's rejection stands.
- **It also touches coupled core.**
  - `journal.mark_dirty` and `journal.begin` both resolve by path (J:131 `_entry_id`, J:271).
  - The catalog needs by-id variants of `fastest_copy`, `get_residency`, `update_residency` and
    `update_meta` (C:151/220-246).
  - `transfer.copy` needs an id threaded through its existence check, meta read and recording
    (T:83, :92, :105-118).
  - `_serve` changes as well, and with it `open`'s properties from cycle 4.

  It leaves `catalog.rename` unlocked, so it does not close the replay bullet above.

## 7. Same-shape calls for the Q3 dated line (path lock held, catalog written by path)

- `evict_copy`: O:1270-1288. It reads and drops by path, and `tier.delete` uses the logical path.
- `_set_meta`: O:594-615. It calls `update_meta`, `journal.queue` and `update_residency` by path, and
  each tier's `set_meta` at the path.
- `_record` stage branch: O:820-841. It reads `entry.path` by id *before* `locks.hold(entry.path)`, then
  writes by path with no source lock.
- `_mark_handle_dirty`: O:730-732. It reads the path by id before the lock. A move in between makes
  `write` raise `LookupError`.
- `commit_row` → `_commit_hop`:
  - O:870-879 reads the path by id before the lock, then O:885-930 does the work.
  - In the source branch, `catalog.get(path)` (O:902) returns `None` if the ancestor landed before the
    lock, and `entry.source_path` then raises `AttributeError`.
  - The stage branch's `transfer.copy`, the `update_residency` at O:923, `queued_for` and `mark_dirty`
    at O:929 are all keyed by path, outside the source lock.
- `commit_upstream` (O:939-955) and `commit_to_shared` (O:976-987) write residencies and settle queue
  rows by path.
- `open`:
  - `catalog.touch` at O:659 runs after the source lock is released, and silently misses after a move;
  - `_serve`'s stage-to-stage promotion (T:105-118) records by path without the source lock.
- `create` and `mkdir`: O:346-376 and O:324-334. `_require_parent`, then `catalog.add(path)`; a parent
  moved in between leaves an entry under a dead parent path.
- `unlink` and `rmdir` → `_remove`: O:483-529, every write by path. This class overlaps the identity
  Non-goal.
