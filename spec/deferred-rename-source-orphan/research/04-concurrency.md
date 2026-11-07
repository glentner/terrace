# Research 04 — Lock order and races around the candidate fix

Code reading only; nothing here was reproduced under threads. Anchors are at the worktree head.

## 1. Today's concurrency map

**Threads in a live mount.** mfusepy's `FUSE(...)` runs libfuse multi-threaded (`nothreads` not passed,
`fuse/mount.py:308-310`). It runs on `sosfs-fuse`, and its callbacks (`mkdir`, `create`, `rename`,
`unlink`, `rmdir`, `fsync`) run concurrently. The `sosfs-writeback` thread runs
`on_scan` → `_due` snapshot → `on_commit` → `commit_row` (`writeback.py:107-120`). `sosfs-evict` runs
`_drop_dirty` → `commit_upstream` (`evict.py:151`). Both pumps start at `mount.py:222-223`. `drain` runs
only after the FUSE thread joins and the pumps stop (`mount.py:337-344`, `250-253`), so it is single-threaded.
`flush()` has no production caller in `src/`; it is engine API and tests only.

**Who runs what.**

| Work | Thread(s) | Locks taken |
|---|---|---|
| `_execute_namespace` (`overlay.py:889-970`) | scheduler via `commit_row:755-757`; drain/flush (quiescent) | **none** |
| `_vacate` source removal (`972-997`) | FUSE, inside `mkdir:313`/`create:335`/`rename:425` | caller's path lock(s) |
| `_purge_source` (`512-528`), `_remove` + `journal.cancel` (`504`) | FUSE, under the caller's lock | caller's path lock(s) |
| Data commit to source (`_commit_hop:776-809`) | scheduler (`762-767`), eviction (`818`), FUSE `fsync` (`707-708`) | `lock(entry.path)` |

Only one thread runs `_execute_namespace` in production. Every FUSE thread and the eviction thread race it.

**Races present today (pre-existing, unreported):**
- **Namespace rows have no moot guard.** Data rows re-check under the lock and settle through
  `_settle_moot`, which swallows `LookupError` (`762-774`). Namespace rows do neither.
  - `_vacate` purges the tombstone (`997`), and the `writeback` FK cascades the row away
    (`schema.sql:105-107`, `catalog.py:161-163`). The scheduler's stale `_due` row then reaches
    `by_id → None → journal.settle` (`892-893`), which raises `LookupError` (`journal.py:209-213`).
  - The exception escapes `on_commit` and kills the pump silently (§ 10). The same happens mid-execution:
    the settle at `948` raises inside the transaction, the transaction rolls back, and the `commit`
    intent from `907` is left neither confirmed nor rolled back, so `journal.pending()` stays non-empty.
- **A cancelled row still executes.** Queue `rename a→b` (row R, captured in `_due`), then `unlink b`.
  `_remove` cancels R and queues `unlink` against `source_path = a` (`500-508`). The scheduler then runs
  the stale R and renames `a→b` on the source. Its settle raises, which rolls back `advance_source_path`
  (`957`). The `unlink` row deletes `a` (an ENOENT it tolerates), so `b` is orphaned with the old bytes:
  defect 1's resurrection, reached by threads instead of by order.
- **Data commit vs rename replay, same entry.** `_execute_namespace` reads `source_path = P1` (`906`).
  The eviction or fsync commit (holding `lock(P2)`, which the replay never takes) writes the new bytes
  at `P2` and records `source_path = P2` (`784`, `808`). The replay's `rename(P1, P2)` (`928`) then
  replaces them with the old bytes, and the residency is already clean, so the staged copy is
  evictable. **Silent data loss**, independent of the candidate.
- **`_vacate` vs a scheduler delete.** The scheduler reads tombstone T at P (`891`, `906`). `create(P)`
  runs `_vacate` and lands the new file on the source (when no stage admits, `337`, `352`). The
  scheduler's `delete(P)` (`921`) then removes the new file. The window is narrow, but the loss is real.
  The settle-first ordering at `948` stops the follow-on `purge(path)` from deleting the new entry.
- Path locks don't cover descendants. A directory `rename` holds `lock(old, new)` only (`408`), and
  `catalog.rename` rewrites every descendant's path (`catalog.py:165-177`) while another thread holds
  `lock(child_old_path)`. The child's `transfer.copy` then fails with `LookupError`
  (`transfer.py:77-78`), or reads a moved staging file.

## 2. Candidate: nested locks / AB-BA?

As written, replaying other entries' rows inside `_vacate` and `_purge_source` adds no path-lock
nesting, because `_execute_namespace` takes none. The deadlock appears if the fix serializes replay
against data commits by making `_execute_namespace` take `lock(F.path)` for the replayed entry F.
`create(/a)` holds `/a` and replays F at `/b`, while `create(/b)` holds `/b` and replays an entry at
`/a`. That is the nested `hold()` § 10 forbids (`locks.py:54-66` sorts only within one call). Rule:
**replay must never take a path lock.** Serialization has to come from a lock ordered strictly below
the path locks.

## 3. One engine-level re-entrant "source namespace mutex"

Proposed order: **path locks → source mutex → `_handles_mutex` → SQLite write lock.**

**Checking for reverse acquisition.** No reverse acquisition exists in today's code:
- Inside the would-be mutex region: `_execute_namespace`, `materialize_dirs` (catalog read plus
  `tier.mkdir`, `transfer.py:29-37`), `transfer.copy` ("caller holds the lock", `transfer.py:73`) and
  `_commit_hop` take no path lock. `_commit_hop` takes `_handles_mutex` (`788`), which never takes
  anything else.
- `_ensure`/`_populate` (`244`), `_record` (`735`) and `_mark_handle_dirty` (`661`) do take path locks,
  and none is reachable from replay or a commit.
- `_vacate` and `_purge_source` run outside `db.transaction()` (`425-427`, `502-503`).

Invariants the plan must state, since they are easy to break later:
- (a) Nothing holding the mutex calls `locks.hold`, `_ensure`, `getattr` or `readdir`.
- (b) Nothing acquires the mutex inside `with db.transaction()`. `BEGIN IMMEDIATE` waits on
  `busy_timeout` (`db.py:125-140`), so an inversion there surfaces as `OperationalError`, not a hang,
  but it is still a failure.
- (c) The mutex is an `RLock`, because `flush` → `_execute_namespace` and `_vacate` → replay re-enter it.

**Cost.** If the mutex spans the whole commit, one multi-GB copy to the source blocks:
- every namespace replay;
- every `create`/`mkdir`/`rename` over a tombstone (a FUSE callback, stalled for the transfer's
  duration);
- every other source commit. Today the scheduler, eviction and fsync can commit different files in
  parallel.

That is a user-visible latency regression on a FUSE mount.

**Narrower alternative (recommended): stage outside, publish inside.**
- `PosixTier.put` already writes to `<root>/.sosfs/partial/put-*`, then calls `os.replace` into
  `final`, which it resolves *before* the copy (`posix.py:132-146`). Split it into
  `stage(path) → temp` (bytes, no mutex) and `publish(temp, P)`.
- Under the mutex: re-resolve P(E), replay or vacate whatever occupies P, `materialize_dirs` on the
  physical ancestors, `os.replace`, then record `source_path` for E and for each created ancestor in
  one transaction.
- Mutex hold time drops to a few metadata syscalls plus one transaction. This changes the Tier
  protocol. S3 `put` is one PUT with no temp (`s3.py:181-187`), so S3 either holds the mutex across
  the PUT (simple and slow) or stages to a temp key and copies server-side (egress, as GOAL Q2 notes).
- **Optimistic put-then-revalidate** works for the single-file case. If `source_path` moved during the
  put, rename the stray object to the new P(E). It breaks for R2: a put whose ancestor was renamed
  mid-flight re-creates the old ancestor through `materialize_dirs` and leaves a stray directory to
  clean up. Not recommended.

**Required regardless.** Reading E's and its ancestors' `source_path`, the publish, and recording
must sit in the same mutex region as `advance_source_path` (`957`). Record exactly where the object
landed, even if the logical suffix went stale under a concurrent ancestor `rename`. Later replays
resolve through `source_path`, so a truthful record is enough.

## 4. Can the suite see it, and how to record it

The suite is single-threaded (`tests/engine/test_locks.py` is the only threaded engine test, and it
covers the registry). It cannot see intra-operation races (§ 1, rows 3-4). **The stale-snapshot class
is deterministic, though**: call `scheduler.due(now)`, then do a user op (`unlink`, `create` over a
tombstone), then `commit_row` each captured row. That reproduces the `LookupError` pump death and the
cancelled-row orphan without threads. It fits R7's "advance a single scheduler poll" if the model
splits the poll into scan and commit.

The existing seed `issues/freethreaded-concurrency-stress.md` doesn't cover this. It names
`Handle` fields, `initialize_logging` and the `ArchiveTier` maps. Its R1 (32 threads, oracle after
`flush()`) might hit it by chance, but nothing names replay vs commit, the unlocked
`_execute_namespace`, or lock order.

**Recommendation:**
- **In this cycle:**
  - The mutex with publish-under-mutex. The candidate's read-compute-act is unsafe without it, and on
    the eviction and fsync routes it is no safer than today.
  - A moot guard and `LookupError` tolerance for namespace rows, mirroring `_settle_moot`, with the
    intent rolled back on a moot exit.
  - The deterministic snapshot test.
  - A § 10 sentence giving the lock order and rules (a)-(c).
- **Follow-up (amend the seed):** an R for concurrent replay vs commit, plus the descendant-lock gap
  from § 1, recorded in `AGENTS.md` § Known open violations, since this cycle adds no thread-level test.
