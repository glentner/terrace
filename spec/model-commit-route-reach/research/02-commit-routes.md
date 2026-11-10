# Research 02 — commit routes into `_commit_hop` and the census

Read at `cf0d16e` (src/ identical to base `980f998`). Read-only; nothing run.

## 1. Callers of `_commit_hop` (overlay.py:966)

Direct callers, all in overlay.py:

| Caller | Site | Reached from |
|---|---|---|
| `commit_row` | :960 (only when `row.op == 'data'`, guard :948) | writeback.py:97 `poll`, :116 `on_commit`; drain.py:100 (namespace rows only, so never a hop); model :287, :328 |
| `commit_upstream` | :1028 | `fsync` :879 (commit policy); `flush` :1088; evict.py:151 `_drop_dirty`; drain.py:106; model :231/:238, :254/:262, :281, :298, :336 |
| `commit_to_shared` | :1066 | rescue.py:68 only; the model never calls it |

Nothing else reaches it: `start`/`stop`, `_serve`/`open`, `truncate`, `rename`, `_remove`, `_purge_source` and `transfers.copy` neither call a commit nor touch writeback rows.

Model rules that reach a hop to the source, and the label the census gives each:

- `write_and_fsync` :207 → `fsync` → `commit_upstream`: **fsync**.
- `rewrite_rename_commit` :228, both calls: **direct**.
- `evict` :331 → `commit_upstream`: **eviction**. `EvictionManager` is never driven, since build_spec sets no quota.
- `poll_once` :301, `poll_next_due` :307, `drain_by_polls` :340 → `WritebackScheduler.poll` → `commit_row`: **scheduler**.
- `commit_scanned` :328 and `rename_scan_unlink_commit` :287 call `engine.commit_row` themselves: also **scheduler**. See finding A.
- `rename_reuse_commit`, `commit_entry`, the first call of `rename_scan_unlink_commit`, `quiesce_and_flush` and `teardown`: **other:<rule>**. The flush paths cannot score: `flush` runs every namespace row first (:1078-1080), so no rename is queued when its data commits.

## 2. Does `commit_row` mean scheduler? Can a namespace replay reach a hop?

`commit_row` reaches `_commit_hop` only for a data row. A non-data row goes to `_execute_namespace` (:948-950). Namespace replay never commits data. `_execute_namespace_locked` (:1105-1212), `_replay_through` (:1302), `_clear_inner_target` (:1275), `_vacate` (:1317) and `_place_parents` (:1229) call only further namespace replay, source mkdir/rename/delete/set_meta, `_purge` and catalog writes. `flush` commits data through `commit_upstream`, which has no `commit_row` frame, so the census walks out to the model rule. No call path nests `commit_row` under `fsync` or `evict`. The innermost-match rule is sound, and the census docstring's claim that namespace rows never reach `_commit_hop` is correct.

**Finding A (attribution scope).** "scheduler" means "`commit_row` on a data row". That matches the GOAL's literal definition (GOAL:78), but it also counts the model's direct `commit_row` calls (`commit_scanned`, `rename_scan_unlink_commit`), which are not scheduler polls. The GOAL's Problem section describes a *poll* (GOAL:18, :47-49). If the plan wants R1/R2 to show reach through the poll rules, record the innermost `OverlayMachine` rule next to the route (e.g. `scheduler/poll_next_due` vs `scheduler/commit_scanned`). Otherwise the floor can be met by `commit_scanned` alone.

## 3. The queued-rename test

**When it reads.** The census reads under the caller's path lock, before `_commit_hop` takes `_source_lock` (:982). The model is single-threaded: the scheduler is never started (test_model.py:94) and there is no `EvictionManager` thread. So nothing runs between the read and the hop. Inside the hop, no rename row can run or settle. `_place_parents` only mkdirs ancestors and sets their `source_path`. `transfers.copy` writes intents and residency. The only writeback rows the hop touches are data rows (:1000-1010). Reading at the start is therefore equivalent to reading under the source lock. (With live pump threads it would race: `_execute_namespace` takes only the source lock. That is out of scope here.)

**Own.** Rename rows are keyed to the moved entry. `catalog.rename` runs first, then `journal.queue(new, 'rename')` (:486-487). The tombstone at the old path is a fresh entry with `source_path` NULL and no rows (:491-493). Tombstones cannot carry a queued rename: `_remove` deletes every row of the entry (`journal.cancel`, :562) before `mark_deleted` and the unlink/rmdir queue (:565-566).

**Removed entries and reused ids.** A rename row whose entry has been removed cannot exist. A purge cascades to writeback rows (schema.sql:107 `ON DELETE CASCADE`, foreign keys on, db.py:96), and entry ids are reused only after a purge. The census matches on `row.entry` against `entry.id`, never on row ids, so writeback-id reuse (journal.py:213) cannot confuse it. `entry.path` is UNIQUE (schema.sql:60), so `catalog.get(path)` returns the live entry, which is the same one the hop re-reads at :983. A tombstone path is never committed (`commit_row` rejects deleted entries at :952; the hop would fail on `None`).

**Ancestor.** A logical-ancestor reading matches the GOAL's wording ("a directory above it"). `catalog.rename` rewrites the whole subtree's `path` (catalog.py:171-183), so the moved directory's current path is a logical ancestor of everything it carried, and of anything created or moved into it afterwards. `PurePosixPath.is_relative_to` compares whole components, so `/d2x` is not counted under `/d2`. Cases where this reading and the physical lag differ:

- **Over-count, no lag.** The census counts these, but the commit lands at the logical path:
  - a directory whose rename is queued while it has no `source_path` (mkdir still queued, so there is no source object to move; :1153 skips it);
  - a round-trip rename A→B→A, or any rename row where `_target(path) == source_path`;
  - the second row of a collapsed chain after the first has already moved the object (:1151-1153).
  
  These are queued-rename commits by the GOAL's literal definition, so they are not miscounts against the contract. They are reach that never stresses the lag.
- **Under-count in `lagging`.** Take a file created (or first committed) inside a directory whose rename is queued. It has `source_path` NULL, so the hop writes at `_place_parents(path)`, i.e. `_target` under the directory's *old* source path (:984, :1216-1227). That is off the logical path, but `lagging` (route_census.py:104) requires `source_path` to be set and so misses it. The census docstring (:14-16) says `lagging` is "the case a commit at the logical path gets wrong", which overstates what it counts.

**Suggestion.** Define the meaningful subset as `where = entry.source_path or engine._target(entry.path)` and count a commit when `where != entry.path`. `_target` reads only the catalog (no clock, no writes). This catches the created-inside case and drops the no-lag cases above.

## 4. Side effects of the census reads

There are none. `catalog.get`/`by_id` (catalog.py:129-138) and `journal.queued` (journal.py:188-193) are plain SELECTs on the calling thread's autocommit connection. That connection already exists, because the engine was started on the same thread (db.py:109-117). The hop is never called inside an open transaction, so the reads join nothing. None of them calls `engine.clock()`. The census's clock-equality assertion is a valid guard.

## 5. Deadlines in the 3-tier model stack (project, scratch, tmp; conftest.py:45-61)

`boundary_policies` (writeback.py:27-36) maps `{'project': scratch.writeback, 'scratch': tmp.writeback}`. Both are the default `WritebackSpec` (spec.py:88-90: delayed, delay 60, no `max_interval`), so `deadline = last_write + 60` (writeback.py:79). Writes land on tmp, because `eligible_tier` picks the fastest stage with no quota (overlay.py:202-211).

- **tmp→scratch data row:** `last_write` is the first write per handle (`_mark_handle_dirty`, :801-805; or `create` :411). Deadline +60. `mark_dirty` refreshes `last_write` and keeps `first_dirty` (journal.py:153-155).
- **scratch→project data row:** created or refreshed by the tmp→scratch hop at the hop's `now` (:991, :1010), not at the write. In `poll_next_due` at time T it lands at T+2: the hop ticks once at :990 and again at :991. Deadline = that + 60. A later rearm also moves it (:1002).
- **rename row:** queued with `to_tier = project`, so it ages on scratch's policy. Deadline = rename time + 60. It is never refreshed: each rename inserts a new row.

`due` is inclusive (`<= now`, :87) and sorts namespace rows first (:88). A poll at the scratch→project row's deadline therefore commits it through `commit_row` → `_commit_hop(…, 'scratch', 'project')` while a rename queued *after* that row's last refresh is still not due: its deadline is later by the gap, at least one tick. The scheduler hit is real.

**Finding B (scenario in the GOAL is one hop short).** "A write, a rename of that file, then a poll past the data row's deadline and short of the rename row's" (GOAL:46-47) commits only **tmp→scratch**, which the census does not count. The scratch→project row it creates is born at about the write time + 62. That is later than the earlier rename's deadline whenever the rename came less than about 62 ticks after the write. So the rename runs first, and the source commit then sees no queued rename. A scheduler-route hit needs **write → poll (tmp→scratch) → rename (the file or an ancestor) → poll at the scratch→project deadline**. The plan's structural reach has to produce that ordering. A tighter `poll_next_due` alone will not.
