# 01 — How the three calls tell their entry from one that took its id, with no schema change

Read at `5893e1f` (its `src/` and `tests/` equal `c7d2049`). Written by the planner; the prototype is a
detached worktree beside `$WT` (PLAN § 6).

## 1. Where an id is freed, and under which locks

`entry.id` is reused only after the row holding the table's largest id is deleted, and a row is deleted only by
`Catalog.purge` (`catalog.py:166-168`, `DELETE FROM entry WHERE path = …`). No other statement in `src/` deletes
from `entry` (`git grep 'FROM entry' src`). `Catalog.purge` has four callers, all in `overlay.py`:

| Site | Purges | Locks held | Transaction |
|---|---|---|---|
| `_remove` `:534` | a live entry with no source copy, or one a rename replaces | path locks only (§ 10: "the mutex does not cover user removals") | inside `with self.db.transaction()` |
| `_execute_namespace_locked` `:1121` | an `unlink`/`rmdir` row's tombstone | source lock | inside the settle transaction |
| `_execute_namespace_locked` `:1142` | a rename row's own tombstone at its old path | source lock | inside the settle transaction |
| `_vacate` `:1267` | a tombstone at the path a new or moved entry takes | source lock | none (autocommit) |

So the named entry's row goes in one of two ways: `_remove` purges it directly, or `_remove` tombstones it
(`mark_deleted`, same row, same id) and a replay purges the tombstone (`:1121`). The rename tombstones that
`:1142` and `:1267` purge are rows a rename added (`overlay.py:457`), never a live entry, so they are never a
call's named entry.

`transfer.py:59` builds a second `Catalog` over the same database; it never purges.

## 2. Families rejected

- **Under the source lock** (Q1's example). `_remove`'s purge takes path locks only. Recording it under the
  source lock means `unlink` and `rmdir` of an entry with no source copy wait out every commit to the source and
  every promotion off it, and § 10's "the mutex does not cover user removals" changes. It would not remove the
  race in § 3 either: purges can land during the call's own first lookup, which takes no source lock.
- **Identity by value** (`ctime`, `mtime`, `size`, `mode` beside the id). Reproducible by a purge and a reuse:
  the clock is injectable and tests run a fixed one; and a `chmod`/`utimens` of the named file during the wait
  changes `ctime` (`_set_meta`, `overlay.py:588-605`), which would refuse a call whose file was never purged.
  Fails R6 and the GOAL's outcome ("behaves as it does today").
- **No reuse within the process** (assign `id` explicitly from an in-process high-water mark in
  `Catalog.add`). Small and sound, but R7 says the existing tests pass unchanged, and five of them assert that
  the id *is* reused (`test_replay.py:911`, `:1202`, `:1227`, `:1578`, `:1629`). It also leaves R1–R3's route
  ("a file the catalog adds afterwards takes its id") unreachable, so their WHEN clauses never fire, and R6
  forbids the id alone as the fact. Rejected by R7.
- **Refuse on any purge during the wait.** Q4 and R4 name it as failing.
- **A generation of additions** (refuse when the catalog added anything). Refuses on a `create` of another
  file during the wait: R4.
- **A per-id purge map for the mount's life.** Sound, but it grows with every distinct id ever purged (a build
  that creates and deletes millions of temporaries), and still needs § 3's ordering.

## 3. The purge watch

A *watch* is a set of purged ids that a call opens before its first lookup and closes when it returns. Every
purge adds the purged row's id to every open watch, **inside its own write transaction**, after the `DELETE`
and before `COMMIT`. A call whose watch holds its entry's id refuses with `ENOENT` at its lookup under the source
lock.

Why that order. Let C be a call that holds its path lock, opens its watch, and then reads entry E at `path`
(`_ensure`, one `SELECT`, which sees the last committed state). Let P be a purge transaction.

1. **If P deletes E's row, P began after C's read.** During P's transaction nothing else commits
   (`BEGIN IMMEDIATE` holds SQLite's write lock), so the committed state has E at the path P deletes, X. If
   X is `path`, P is a `_remove` holding `path`'s lock, which C holds, so P cannot run until C returns; a
   replay purge never deletes a live row (each deletes a tombstone, and `path` is `UNIQUE`). If X is not
   `path`, C, reading during P, would see E at X, not at `path`. So C's read precedes P's `BEGIN`, which
   precedes P's record: the record lands in C's watch.
2. **If P deletes another row with E's id (a predecessor), P committed before C's read**: E was inserted after
   P's commit (ids are unique at any instant) and C read it. P's record precedes its commit, so it precedes C's
   read.

So a record of E's id that lands **after C's read** can only be E's own purge. A record that lands between
opening the watch and the read is ambiguous (case 2 with P after the opening, or case 1 with P after the read
but before C checks). C therefore **names its entry in a loop**: reset the watch, read, and if the id read is
already in the watch, go round again. Once a read passes that check, the watch holds E's id later if and only if
E itself was purged after C named it. A reset is a fresh opening: case 1 still puts E's purge after it.

Recording after commit would let a predecessor's record land after C's read (a false refusal, R4); recording
before `BEGIN` would let E's own purge record before C's watch opened (a miss, R1–R3). The `_vacate` purge is
autocommit today and is wrapped in a transaction so every purge records inside one.

**Superseded by the adversarial review (2026-11-09).** The loop above is wrong: a whole reuse route landing between the read and the loop's test makes the loop read again and accept the file that took the id, and the call cuts it. The design now clears the watch atomically with the naming read: under the watch mutex for a cataloged entry, inside the insert's transaction for an adopted one, with the watch reaching `_ensure` through a thread-local because `_ensure`'s signature is fixed by existing tests. Every record made before that step is a predecessor's, and every record made after is the named entry's own purge. See PLAN § 2.1, deviation row 2 and § 4.1.

**What it does not need.** No schema change (R5): the watch is in-process and lives for one call. No new holder
of the source lock. Ids are still reused, so the existing tests that assert reuse pass unchanged (R7).

**Lock order.** The watch list and its ids are guarded by one new `threading.Lock`, taken (a) by `_purge`
inside its transaction, so after the SQLite write lock, and (b) by the call to open, reset, test and close its
watch, under its path lock and, for the test at the lookup again, under the source lock. Nothing is acquired
while holding it: a leaf at the end of § 10's order (path locks → source mutex → `_handles_mutex` → SQLite
write lock → watch mutex). `_handles_mutex` cannot serve: `_purge` would take it after the SQLite write lock,
against § 10's order.

**Bounds.** One watch per call in flight; a watch holds the ids purged while it is open (a flush during a long
wait adds one per tombstone it purges). The naming loop repeats only when a purge of the very id it read is
recorded inside its own read; each repeat needs another entry to take that id and be purged again, so it ends
unless that happens without pause (a livelock no deterministic test can show; risk, PLAN § 5).

**Failure.** If a purge transaction rolls back after its record (a failed `COMMIT`; in all four sites the
purge is the transaction's last statement), the id stays recorded with its row still present, and a call whose
named entry is that row refuses with `ENOENT`. That entry's own removal failed; no other entry's purge can cause
it (R4 speaks of other entries).

## 4. The code

- `Catalog.purge` returns the purged id (`DELETE … RETURNING id`, as `Catalog.add` already uses `RETURNING`).
- `overlay.py`: `PurgeWatch`; `OverlayEngine._watches` and `_watches_mutex`; `_watch()` (a context manager);
  `_purge(path)` (all four sites, `_vacate`'s wrapped in `db.transaction()`); `_name(path, watch)` (the loop);
  `_lookup_again(entry, watch)` adds "or the watch holds the entry's id"; `open`, `_serve` and `truncate` open
  a watch under the path lock and pass it; the cut's inline check becomes `_lookup_again`, the same test.

Prototype: 105 lines changed in `overlay.py`, 3 in `catalog.py`; 542 passed under `CI=true` (530 + 12 new);
`tools/lint.sh` green.

## 5. What a deterministic test can and cannot see

Seen (prototype, `test_replay.py` alone, 97 tests): removing the watch from the lookup (M1) or the record from
`_purge` (M2) turns R1–R3's four tests red, and M2 the purge-after-first-lookup test too; a watch that matches any
purge (M3) turns the six R4 tests red, and six existing replay-during-the-wait tests; skipping the second look
(M4) and resetting the watch after the read (M5) each turn their naming test red; each site's lookup without the
watch (M6 cut, M7 open, M8 promotion) turns that site's tests red. Not seen, equivalent under any deterministic
test: leaving the `_vacate` purge outside a transaction (E1) or the rename-tombstone purge unrecorded (E2),
since those rows are never a named entry, and recording after `COMMIT` rather than before, which differs only
for a thread that runs between the two.
