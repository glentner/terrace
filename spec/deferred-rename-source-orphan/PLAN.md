# PLAN — A commit before a queued rename leaves no orphan, and the replay converges

> **Status:** Draft for review · **Last updated:** 2026-11-06
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (`appetite: big`), digested in
> [`research/00-digest.md`](research/00-digest.md). Every design element traces to a GOAL R-ID.

## 1. Summary

Every physical touch of the source resolves through one function of the catalog: an entry's
**target** T(E), its parent's committed source path (or the parent's own target) plus its name.
Queued namespace rows replay against the entry's *current* target instead of the path they were
queued at, and a data commit to the source writes where the source already holds the entry
(`source_path`), else at T(E), so the queued rename stays the only thing that moves an existing source
object (GOAL Q2). The rule that no two entries share a physical place is enforced when the user op
runs: `_vacate` replays the queue in order through whatever still occupies the place a new name
resolves to. One re-entrant source-namespace mutex serializes everything that changes the source's
namespace. The stateful model gains rules that commit one entry, poll the scheduler (also split into
the pump's scan and commit), fsync under the commit policy, evict, and drain deadline by deadline, so
the interleavings behind both defects are covered, not just fixed. It fits `appetite: big`: five
phases, two engine slices first, every region already named in the GOAL, no schema change.

## 2. Design

### 2.1 Placement: the target of an entry

`OverlayEngine._target(path)` (new, `src/sosfs/engine/overlay.py`): `/` for the root; otherwise the
parent's `source_path` when it has one, else the parent's own target, joined with the entry's name.
It reads only the catalog. For an entry whose ancestors carry no queued rename, T(E) is its logical
path, which is why `_populate` may still list the logical path (§ 5's `_populate_tree` rule is
untouched: an unlisted directory is adopted and never renamed, nor is any ancestor).

`OverlayEngine._place_parents(path, now)` (new): for each ancestor the source lacks (catalog
`source_path` is `NULL`), outermost first, create it on the source at its own target with the
**catalog mode of that entry**, intent-bracketed (`commit`, `{'op': 'mkdir', 'source_path': …}`), and
record its `source_path` in the transaction that confirms the intent. It returns T(path). Nothing on
the source side resolves a mode by path any more: `materialize_dirs` looks ancestors up by path
(`transfer.py`), which is the root of the `EACCES` class (C1 in research 02), so source-side placement
goes through `_place_parents` and `materialize_dirs` then finds every ancestor present. Stage tiers
keep `materialize_dirs` unchanged: stage copies move with the catalog at rename time.

### 2.2 Replay against the current target (defect 2; R5, R6)

`_execute_namespace(row)`, under the source mutex (§ 2.5), first asks the journal whether the row it
was handed is still queued (§ 2.6). Then, per op:

- **`mkdir`**: when the entry has no `source_path`, place its ancestors and create it at T(E),
  recording `source_path` in the confirming transaction; when it has one (an ancestor placement or a
  data commit already made it), settle as a no-op. The recorded `detail['path']` is no longer read.
- **`rename`**: when `source_path` is set and differs from T(E), place T(E)'s ancestors and move the
  object `source_path → T(E)`, then `catalog.advance_source_path(source_path, T(E))`, which rewrites
  every descendant's prefix (one source rename relocates a subtree). When they are equal, skip the
  tier call entirely: `S3Tier.rename(p, p)` destroys the object on an obstore store, and POSIX's
  `os.replace(p, p)` hides that (research 01 § 4). A chain `a → b → c` collapses into one move to
  `c`; `detail['old']`/`detail['new']` stay in the row for the tombstone rule below and for the audit.
  When the entry is a file with no source residency, the residency is added from a stat at its
  `source_path` after the move (today's branch, resolved physically rather than at the logical path).
- **A directory whose target lies inside its own source object** (found by the adversarial review,
  § 4.1): `mkdir /e; mkdir /e/p; flush(); mkdir /x; rename /e → /x/e; rename /x/e/p → /p;
  rename /x → /p/x`. X's queued `mkdir` places X inside E's object, so T(E) lies under E's
  `source_path` and E cannot move there. Before acting, the row runs, through `_replay_through` and
  skipping its own row, the queued rows of the entries holding the places between E's `source_path`
  and T(E) (here P's move out of E, which does not depend on E: P is E's logical ancestor), then
  recomputes T(E). A target still inside raises `EngineError` rather than settling the row unmoved.
- **`unlink`/`rmdir`**: unchanged: they target `source_path`, then confirm before the purge.
- **`meta`**: applied only at a non-`NULL` `source_path`. Today a `meta` row for an entry with no
  source copy writes at the logical path, which may be *another* entry's object during the lag.
- **Tombstone purge after a rename row.** The tombstone at `detail['old']` is purged only when it is
  deleted, holds no `source_path`, has no queued row, and no entry holds `detail['old']` on the source
  (`Catalog.holder`, new, over the existing `entry_source_path` index). Today's rule purges whatever
  tombstone sits there, which in research 02's P6b cascades away another entry's queued `unlink`.
  A tombstone never carries a rename row: `_remove` cancels every row before it queues the removal.

### 2.3 Vacate through the queue (R5, R6, and the collisions behind R1)

`_vacate(path, mover=None)` keeps its call sites (`mkdir`, `create`, `rename`) and its contract
(clear the place before the new or moved entry takes it), with a wider reach. Under the mutex it
collects the queued namespace rows of every entry other than `mover` whose `source_path` equals
T(path), plus the tombstone-at-`path`'s own rows, and hands them to `_replay_through(rows)`, which
runs queued namespace rows **in queue order** until every collected row has run. It then purges the
tombstone at `path`. `rename` passes `mover=entry.id`, so a rename back onto the entry's own physical
place does not replay itself.

`_purge_source(entry, now)` (a rename replacing an entry) first replays, through the same helper,
the queued rows of entries holding anything under the replaced directory's `source_path`, so a child
whose removal or move-out is queued is physically gone before the `rmdir` (research 02 P4). It also
rolls its intent back on `BaseException` rather than leaking it (today it catches only
`FileNotFoundError`).

*Amended in build (2026-11-06, P3):* a replayed `rmdir` row does the same before it removes its
directory's source object, skipping itself in the prefix. Removing an entry cancels its queued rename
and queues the removal last, so `mkdir /d2, create /d2/a.dat, flush, rename /d2/a.dat → /a.dat,
rmdir /d2, unlink /a.dat, flush` queued the parent's `rmdir` ahead of the child's removal while the
child still sat in `d2` on the source, and `flush` raised `ENOTEMPTY` (on `141aa86` too; only
baseline rules reach it, and the extended model found it at the CI draw). R5's class; the regression
is `test_flush_removes_a_moved_out_child_before_its_old_parent`, in `REPLAY_TESTS`.

`_replay_through` is the single place a namespace op jumps its policy clock. It replays a *prefix*,
which is what makes out-of-order execution safe (a parent's `mkdir` or a child's move-out always runs
before the row that needs it), at the cost of foreground work bounded by the queue's length
(deviation D1).

This one rule closes the collisions research 01 § 3 found at `141aa86`, and research 03 § 2 found the
model would reach once R7 lands: a new file at a renamed-away name (silent loss of the original),
a new file where an unlinked file still sits (silent deletion of the new bytes), and the collision on
P(X) rather than X. Because the rule holds whenever a name is introduced, a data commit's destination
is always free or already the entry's own when the commit runs.

### 2.4 Data commits during the lag (defect 1; R1, R2, R4)

`_commit_hop(path, holder, upstream)`: when `upstream` is the source, under the mutex, the
destination is `entry.source_path`, else `_place_parents(path)`, and `TransferPool.copy` writes there.
`copy` (`src/sosfs/engine/transfer.py`) gains `dest_path` (the physical write path; the read side
already has `src_path`) and `source_path` (recorded by `catalog.update_meta` **inside the transaction
that confirms the copy**, beside the residency row and the usage counter), and its intent detail
records `dest_path`. That moves the `source_path` update out of `_commit_hop`'s second transaction,
where a crash between the two leaves the source holding bytes with `source_path` `NULL` and a later
`unlink` purging with no queued removal (research 01 § 1; a § 1 split present at `141aa86`).
`_commit_hop` keeps its handle re-arm, `changed` detection and next-hop `mark_dirty` exactly as they
are. Stage-to-stage hops are unchanged.

All three R1 routes funnel through `_commit_hop` (`fsync` under `"commit"` and the eviction manager
through `commit_upstream`; the scheduler through `commit_row`), as do `commit_to_shared` and rescue,
so one change covers them; `evict.py` and `writeback.py` need no edit. A commit for an entry whose
rename is queued writes the new bytes at the **pre-rename** physical path, the rename row later moves
them (R1's "once the queue drains"). Inside a renamed directory the file lands under the directory's
old prefix beside its siblings, and the directory's rename moves them all (R2, Clarification Q3).

`create` straight onto a passthrough source (no upstream stage) places at T(path) the same way.

### 2.5 Concurrency: one source-namespace mutex

`OverlayEngine._source_lock`, a `threading.RLock`, serializes `_execute_namespace`, `_vacate`'s
collect-and-replay, `_purge_source`, and a data commit whose destination is the source (placement,
copy and recording). Lock order: **path locks → source mutex → `_handles_mutex` → SQLite write lock.**
Rules, stated in `AGENTS.md` § 10:

- nothing holding the mutex calls `locks.hold`, `_ensure`, `getattr` or `readdir` (replay never takes
  a path lock; a nested `hold()` inside replay is the AB/BA deadlock § 10 forbids, research 04 § 2);
- nothing acquires it inside `db.transaction()`;
- it is re-entrant because `flush` → `_execute_namespace` and `_vacate` → `_replay_through` →
  `_execute_namespace` nest.

`_commit_hop` still takes no *path* lock (§ 10's rule about the caller's lock stands); the mutex is
below every path lock. Without it, the read-compute-act of § 2.3 races the scheduler's replay on the
eviction and fsync routes (research 04 § 1 row 3: replay reads `source_path`, a commit writes and
records a new one, the replay's `rename` then overwrites the new bytes). The mutex spans the whole
copy when the destination is the source (deviation D4 records the latency this costs).

*Amended in review cycle 1's remediation (2026-11-06), F2:* `_record` also takes the mutex, below its
path lock, to stat a copy served off the source at its committed source path, so a replay cannot move
the object between that read and the stat (which would leave the old size recorded for good). The
`release` or `fsync` of a source-served copy therefore waits for a source-bound copy in flight, D4's
cost on one more caller. `getattr`'s live stat stays lock-free: a miss leaves the catalog's figure.

### 2.6 Stale scheduler rows (R1's scheduler route, R3)

The write-back pump scans, then commits, with FUSE callbacks running in between (`writeback.py`
`on_scan`/`on_commit`). A row in that snapshot may since have run (a vacate), been cancelled
(`_remove`) or cascaded away (a purge). `writeback.id` is `INTEGER PRIMARY KEY` without
`AUTOINCREMENT`, so the id of a deleted highest row is **reused**. The planner's prototype showed two
silent losses at `141aa86` that follow from looking a row up by id alone:

- a cancelled `rename` row's id is taken by the `unlink` queued in its place; the stale rename runs
  and settles the `unlink`, leaving the old bytes on the source behind an empty queue;
- a settled data row's id is taken by the next hop's row (`mark_dirty` inserts it in the same
  transaction); the stale row finds its holder clean and settles the next hop's row as moot, so the
  data never reaches the source and nothing is queued to take it there.

`Journal.current(row)` (new, `src/sosfs/state/journal.py`) returns the queued row with `row.id` only
when its entry, op, boundary, detail and first-dirty time all match the snapshot's (a refreshed row
keeps its first-dirty time; a new one does not). `_execute_namespace` returns without acting when
`current` is `None`, and `_settle_moot` settles only a current row. This replaces the `LookupError`
that kills the pump today (research 04 § 1 row 1). No schema change: `AUTOINCREMENT` would be one, and
a `SCHEMA_VERSION` bump is a stop-and-ask the design avoids.

*Amended in review cycle 1's remediation (2026-11-06), F3:* checking `current` once, before the act,
is not enough. User removals take no source mutex (§ 2.5 covers changes to the source's namespace, and
`_remove` changes only the catalog and the queue), so an `unlink` can land inside the act, cancel the
row and queue its own op under the same id; the replay's `settle(row.id)` then deleted that op, the
queue read drained and the source kept the object. Every settle of a row read earlier now goes through
`Journal.settle_current(row)`, which deletes the row only while it is still the one read, in one write
transaction: the replay's confirming transaction, its entry-gone branch, `_settle_moot`, and `flush`'s
settle of a data row whose entry is gone (a removal while `flush` commits another file takes that
row's id the same way). The act's effect is recorded either way, so the removal resolves where the
object now is. Settles that read their rows inside the settling transaction (`_commit_hop`,
`commit_upstream`) were already exact. Liveness holds: a row not cancelled still matches and settles.
Still open, and seeded as `issues/freethreaded-concurrency-stress.md` R9: an `rmdir` landing while a
replayed `mkdir` has placed its directory but not yet recorded it reads `source_path` `NULL`, purges
the entry and, by cascade, the replay's intent, so the replay's confirm raises (pre-existing).

### 2.7 The model (R7, R8)

`tests/engine/test_model.py` keeps every baseline rule, `parity`, `real`, `snapshot`, the strategies
and the teardown's statements verbatim, and adds:

| Addition | Why |
|---|---|
| `__init__`: `self.clock`, a spec built as `dataclasses.replace(build_spec(self.root, tiers=3), fsync_policy='commit')`, an unstarted `WritebackScheduler`, `self.scanned = []` | `fsync_policy` is read only in `fsync`, which no baseline rule calls; an unstarted scheduler's `poll` runs synchronously (`halting` is false until started) |
| `commit_entry(path)` → `commit_upstream` | R7: commit one entry's data (routes: fsync-commit and eviction share it) |
| `poll_once(advance)` → advance the clock, one `poll` | R7: one scheduler poll after an arbitrary jump |
| `poll_next_due()` → clock to the earliest deadline, one `poll` | the 1-tick windows where data overtakes a rename (research 03 § 1) |
| `scan_writeback(advance)` / `commit_scanned()` | the pump's SCAN and COMMIT states with user ops between them (§ 2.6) |
| `write_and_fsync(path, data, offset)` | Q7: the fsync route, under parity with `pwrite` |
| `evict(path)` → commit a dirty stage copy, then `evict_copy` | Q7: the eviction route without an `EvictionManager` (no quotas, so the siblings stay unreachable) |
| `rewrite_rename_commit(old, new, data)` → commit, rewrite, rename, commit again, under parity | defect 1 in one step once a file exists; without it the CI seed missed the defect-1 mutant and random seeds caught it about 1 run in 9 (§ 4.1) |
| `rename_reuse_commit(old, new, data)` → commit, rename, create at the old name with data, commit it, under parity | the collisions of § 2.3 in one step; without it the model missed the vacate mutant at the CI seed |
| `rename_scan_unlink_commit(old, new, advance)` → commit, rename, advance the clock and take the pump's snapshot, unlink the new name, commit the snapshot, under parity | the stale-row class of § 2.6 in one step (added in build, 2026-11-06; see below) |
| `drain_by_polls()` → up to 200 times: clock to the earliest deadline, one `poll`; then `queued() == []`, `pending() == []`, source == oracle | R7: convergence after the queue drains from states the new ops produce, in deadline order, never `flush()` |
| `@invariant source_objects_have_one_holder` | every source object but the never-adopted `/seeded.txt` is some row's `source_path`, and no two rows share one |
| `teardown`: after the baseline's flush and assert, stop, start a fresh engine over the same spec and clock, and compare its full `readdir` walk with the oracle | R4 |
| `max_examples` 100 → 1000 | detection: research 03 found the defects on 1 seed in 6 at 100 examples; see D5 |

*Amended in build (2026-11-06):* the third composite. Hypothesis seeds its fixed CI draw from a digest
of the whole `OverlayMachine` source, docstrings included and comments excluded, so any edit to the
class redraws it. The class docstring TECH P3 requires redrew it, and at the new draw test_model passed
with M2 planted, while M2 failed it on 5 of 6 random seeds: the reach was there, the draw missed. Tuning
text for a lucky draw proves nothing, so M2's sequence became one step, as the two composites above did
for M1 and M3 (§ 4.1, 2). The teardown's remount compares the full view, paths, kinds and bytes, and the
oracle side of `write_and_fsync` fsyncs too.

`ADVANCE` is a new strategy (`sampled_from([0, 1, 30, 59, 60, 61, 90, 120])`); the seven baseline
strategies are untouched. Nothing adds `assume`, a precondition, a filter, a skip, a profile, or an
exception handler (R8).

### 2.8 Deterministic regressions

`tests/engine/test_replay.py` gains one test per route and class, each failing on the `141aa86`
engine for cause (an assertion or an engine error, never a missing API), under the names in
`verify/replay_gate.py` (`REPLAY_TESTS`, twelve; `COMMIT_TESTS`, six), plus `DESIGN_TESTS` (one)
guarding a hazard of this design that the baseline does not have (§ 2.2's inner target). Each test
lists the whole tree with `walk(engine)` before acting and ends in `assert_converged`:

- the queue is drained, and no live directory is unlisted (an unlisted directory's `readdir` adopts
  whatever the source holds, which would hide an orphan from the very view it is compared with);
- the set of live catalog paths, taken before any `readdir`, equals the source tree's paths;
- the source tree, with bytes, equals the overlay view read through `readdir`/`open`/`read`;
- a fresh engine over the same roots reads the same view (R3, R4).

*Amended in build (2026-11-06), foreman ruling:* the sixth `COMMIT_TESTS` entry,
`test_create_on_the_source_inside_renamed_directory`, covers a `create` straight onto the source inside
a directory whose rename is queued (§ 2.4's passthrough create). On `141aa86` the file lands at the new
prefix and the queued rename then fails with `ENOTEMPTY`. No mutant below reaches it: M1 is
`_commit_hop`'s placement, and `create` places through its own line.
*Amended in build (2026-11-06), foreman ruling:* every named fix gets a fix-removal mutant, so M6
below plants that line's removal and must fail this test.
*Amended in build (2026-11-06), foreman ruling:* the `rmdir` pre-replay P3 added to § 2.3 (a replayed
`rmdir` first replays the queued rows of entries its source object still holds) gets M7, which disables
its guard and must fail `test_flush_removes_a_moved_out_child_before_its_old_parent` and the model.

**Fix-removal mutants.** A test that fails on the baseline at an early, design-specific assertion
shows nothing about its end-state checks, and a model that fails on the baseline may be failing on
something other than the defect. So `replay_gate.py mutants` plants nine mutants into a scratch copy
of HEAD's engine, one at a time, and requires named regressions to fail on each, and test_model to fail
under `CI=true` (shrinking off) on the first three (and on M7, added in build; M8 and M9 came with review
cycle 1's remediation):

| Mutant | Regressions that must fail | Model |
|---|---|---|
| M1 a data commit writes at the logical path | `test_commit_before_queued_rename_leaves_one_object`, `test_commit_inside_renamed_directory` | fails |
| M2 a stale row is looked up by id alone | `test_scheduler_row_id_reused_after_cancel`, `test_scheduler_data_row_id_reused_by_next_hop` | fails |
| M3 `_vacate` ignores other holders | `test_new_file_at_a_renamed_away_path`, `test_new_file_where_a_removed_file_still_sits` | fails |
| M4 any tombstone is purged after a rename | `test_flush_purges_only_the_tombstone_a_rename_left` | not required |
| M5 the inner-target pre-replay is skipped | `test_flush_when_a_directory_target_lies_inside_its_own_source_object` | not required |
| M6 a `create` onto the source lands at the logical path (added in build, 2026-11-06) | `test_create_on_the_source_inside_renamed_directory` | not required |
| M7 an `rmdir` row runs before its moved-out children (added in build, 2026-11-06) | `test_flush_removes_a_moved_out_child_before_its_old_parent` | fails |
| M8 a served copy is stat'ed at its logical path (review cycle 1, F2) | `test_size_of_a_file_created_on_the_source_inside_renamed_directory`, `test_create_on_the_source_inside_renamed_directory` | not required |
| M9 a replayed row is settled by id alone (review cycle 1, F3) | the five F3 regressions below | not required |

*Amended in review cycle 1's remediation (2026-11-06):* `assert_converged` also compares sizes: every
size `getattr` and the residency rows report equals the file's byte count, on the engine and on the
remount, with no `bypass_size` configuration. The gate's `REVIEW_COMMIT_TESTS` (F2:
`test_size_of_a_file_created_on_the_source_inside_renamed_directory`) and `REVIEW_REPLAY_TESTS` (F3:
`test_scheduler_replay_settles_only_its_own_row`, `test_flush_replay_settles_only_its_own_row`,
`test_drain_replay_settles_only_its_own_row`, `test_vacate_replay_settles_only_its_own_row`,
`test_flush_settles_only_the_data_row_it_read`) pass here and fail on the reviewed engine (`67dfaee`);
the F3 tests inject the interleaving on one thread by wrapping the source tier's `rename` (or `put`)
to run a user `unlink` right after the real call. The model is not edited: Hypothesis's fixed CI draw
is a digest of its source.

Anchors are the built engine's text; a reshaped line is re-anchored in the gate by the phase that
reshapes it, never dropped.

### 2.9 Records

- `AGENTS.md` § 5 and § 10 text per § 3 below (same diff as the behaviour); § Known open violations'
  § 5 row narrowed to the deferred siblings, naming `issues/source-path-truncate-and-stat.md`;
  measured counts and timings (§ Commands, § Repository map, § Verification, the `-m integration`
  note).
- `PLAN.md` → Write-back → Deferred-op replay amended to current-target replay and vacate-through;
  § Risks' namespace-replay entry no longer *resolved, Phase 8* without qualification: it names what
  this cycle closed and the siblings it leaves open (R12).
- `issues/source-path-truncate-and-stat.md` (new, `status: unshaped`) carries the siblings and the
  already-wrong sizes, with the seed's reproductions and `file:line` evidence, plus a `ROADMAP.md`
  entry (GOAL Constraints, Q1, Q5).
- `issues/freethreaded-concurrency-stress.md` gains a sketched R for replay against a concurrent data
  commit under threads: the suite is single-threaded and cannot exercise the mutex (§ 5 Risks).
- No live text keeps the toolchain repair's tolerance. `ci.yml` carries none today and needs no edit;
  `AGENTS.md` § Commands' CI paragraph says each of the four jobs is expected to conclude `success`.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | § 2.4 (`_commit_hop` writes at `source_path`/T(E), `copy`'s `dest_path` and in-confirm `source_path`), § 2.2 (rename replay moves it), § 2.5, § 2.6 (scheduler route); `test_commit_before_queued_rename_leaves_one_object`, `test_fsync_commit_policy_before_queued_rename`, `test_eviction_commit_before_queued_rename`, `test_scheduler_poll_settles_data_before_rename` |
| R2   | § 2.1 (T(E) under a renamed parent), § 2.4, § 2.2 (subtree move + `advance_source_path`); `test_commit_inside_renamed_directory` |
| R3   | § 2.2-2.6 together; `assert_converged` in every regression; the model's `drain_by_polls` and invariant |
| R4   | § 2.3 (no shared physical place, so nothing stale survives), § 2.2 tombstone rule; `assert_converged`'s remount; the model's teardown remount |
| R5   | § 2.2 (current-target replay, by-entry modes), § 2.3; `test_flush_after_directory_round_trip_and_reuse` (EACCES), `test_flush_after_directory_round_trip` and `test_flush_after_child_moved_out_of_removed_parent` (stray directory, both forms) |
| R6   | § 2.3 (`_vacate` replays the child's queued removal first); `test_mkdir_over_removed_directory_with_queued_child_removal` |
| R7   | § 2.7 (`commit_entry`, `poll_once`, `poll_next_due`, `drain_by_polls`, invariant) |
| R8   | § 2.7 (additions only); `replay_gate.py model`'s static check against `141aa86` |
| R9   | all of the above; `AGENTS.md` counts; `replay_gate.py suite` |
| R10  | § 2.7 (`max_examples` 1000 for reach); `replay_gate.py seeds` from a `git archive` of HEAD |
| R11  | no workflow change; `replay_gate.py ci` reads the push run's record with no tolerated test |
| R12  | § 2.9; `replay_gate.py records` |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against `AGENTS.md` § Invariants before research (from the GOAL's intent) and again on this
drafted design and the prototype that runs it. Sections touched:

- **§ 1 Intent → act → confirm.** Every new source mutation is bracketed: `_place_parents`' mkdirs,
  the replay's moves and creates (one intent per row, as today), `_purge_source` (now rolling back on
  any exception). The data commit's `source_path` moves *into* the confirming transaction, which
  repairs a split present at `141aa86`. **Confirm before purge** holds: `_execute_namespace` still
  settles and confirms before `drop_residency`/`purge`, and `_vacate` purges the tombstone only after
  every replayed row has confirmed. **Metadata before acknowledgment**: `write`, `_mark_handle_dirty`
  and `truncate` are untouched.
- **§ 2.** No schema change, no `SCHEMA_VERSION` bump: `Catalog.holder` uses the existing
  `entry_source_path` index; `Journal.current` reads `writeback` by id.
- **§ 4.** No adapter edit. The replay skips a same-path rename instead of calling the tier.
- **§ 5.** The section this cycle exists to make true. Sentences amended in the same diff are D1-D3
  below; the `_populate_tree` sentence, "a deferred rename leaves it untouched", "removals target it",
  the `meta` rule and the catalog-vs-bytes authority rule stand as written.
- **§ 6.** "Replayed namespace-ops-first" stands (`due`, `flush`, `drain` unchanged). The "invents
  parents with default modes" hazard is closed on the source side: `_place_parents` takes each
  ancestor's catalog mode by entry.
- **§ 7.** Eviction still commits a dirty copy to the source before dropping it, through the same
  `commit_upstream`; `_drop_dirty` is untouched.
- **§ 10.** A new lock with a stated order and three rules (§ 2.5), added to § 10's text. A namespace
  row made moot no longer raises `LookupError` out of `on_commit`, so it no longer kills the pump.
- **§ 11.** `flush()` completes on the R5 sequences instead of raising `PermissionError`/`ENOTEMPTY`;
  the API stays synchronous.

High-blast-radius paths edited: `overlay.py` `_commit_hop`, `_execute_namespace`, `_purge_source`,
`_vacate` (and `commit_row`'s settle); `transfer.py` `copy`; `journal.py` (a new read method, none of
the four listed functions); `catalog.py` (a new read method, not `META_FIELDS`); `tests/engine/
test_replay.py` and `test_model.py` (extended, never weakened). `writeback.py`, `evict.py`,
`drain.py`, `posix.py` and `s3.py` are not edited. Geoffrey signs off on the fix at review (GOAL
Constraints).

### Deviation justifications

Rows D1-D3 amend sentences of `AGENTS.md` § 5; Geoffrey approved all three at the PLAN/TECH sign-off
(2026-11-06), with the stale-scheduler-row fixes (§ 2.6) ruled in scope as the same replay family;
the amended text lands in the same diff as the behaviour (P1 and P2). Rows D4-D6 add complexity or
cost without bending an invariant. Rows R-a to R-d record how the design reads an R-ID.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| **D1 (§ 5, overturns a sentence).** "`_vacate` is the one place a namespace op jumps its policy clock, executing the queued source removal synchronously" becomes: `_replay_through`, reached from `_vacate`, `_purge_source` and a replayed `rmdir` (§ 2.3, amended in P3), is the one place; it runs the queue *prefix* in order through the rows that free a physical place, which can include other entries' renames, mkdirs and meta rows. A rename row whose target lies inside its own source object uses the same helper (§ 2.2). | R6 (`ENOTEMPTY` on re-`mkdir`: the child's queued `rmdir` must run first), research 02 P4/P5 (rename over a directory whose child's removal or move-out is queued), and research 01 § 3 (a) (b) (d): a new name whose physical place another entry still holds silently loses or deletes data unless that entry's queued departure runs first. | Running only the tombstone's own removal (today) fails R6 and P5. Running only the occupant's row, out of order, breaks parent-before-child ordering (a rename into a directory whose `mkdir` is still queued). Running the whole queue (`flush`) on every collision jumps every row's clock and is the "unbounded namespace work" Q4 warns about with no bound at all; the prefix stops at the last row it needs. Cost: foreground work bounded by the queue length, including S3 copies on an S3 source. |
| **D2 (§ 5, amends a sentence).** "Committing a rename upstream moves the object from `source_path` to the new path and only then advances it" — *the new path* is the entry's current target T(E) when the row runs, not the row's recorded `detail['new']`; a chain collapses into one move. Also overturns the `Journal.queue` docstring ("reproducing the namespace history faithfully") and the `_execute_namespace` comment on `mkdir`'s queue-time path, and `PLAN.md`'s Deferred-op replay sentence. | R5: the queue is a lossy history (`_remove` cancels a purged entry's rows), so replaying recorded paths re-creates ancestors nobody owns (the stray directory) and reads the kind and mode of reused paths that changed (`EACCES`). | Historical replay patched per class (research 02 § 3): fixing the mode leaves `materialize_dirs`' stray directory, and the next row collides with it; every patch needs ownership tracking for materialized ancestors, which is current-target replay by another name. |
| **D3 (§ 5, completes a sentence).** "Adoption sets it to the entry path. `create` sets it only when the file lands straight on the source." gains: a data commit to the source sets it where it wrote (in the confirming transaction), and the `mkdir` row and ancestor placement set it for directories. Both `create` clauses stay true. | § 2.1, § 2.4. A commit already set it at `141aa86` (`_commit_hop`, to the logical path), so the sentence was incomplete before this cycle. | Leaving the sentence as is would leave § 5 asserting a closed list of setters the code exceeds, a review finding against correct code (§ Conventions' same-commit rule). |
| **D4 (§ 10, adds a lock).** The source-namespace mutex (§ 2.5), held for the whole copy when a data commit's destination is the source. While a large file commits to the source, every `create`, `mkdir` and `rename` that reaches `_vacate`, and every `fsync` under the commit policy, waits for it. | Without it the scheduler's replay and a data commit on the eviction or fsync route race on `source_path` (silent loss, research 04 § 1 row 3), and § 2.3's read-compute-act is unsafe on every route. | Staging the bytes outside the mutex and publishing inside it (research 04's recommendation) changes the `Tier` protocol in `posix.py` and `s3.py`, two more high-blast-radius regions, and S3 has no cheap publish (a temp key plus a server-side copy). Optimistic put-then-revalidate re-creates a renamed-away ancestor through `materialize_dirs` (R2 breaks). Whether to seed the staged form as follow-up work is a sign-off question (§ 5). |
| **D5 (cost).** `max_examples` 100 → 1000: `test_model` takes about 25 s instead of about 3 s, so the profile's `verify` takes about 33 s serial instead of about 5 s (and `-n auto` is no faster, since one test dominates), and each R10 seed about 30 s. | R7's windows are 4-7 steps over 21 rules (11 baseline, 10 added); at 100 examples the extended model found the defects on about 1 seed in 6 (research 03 § 1). Measured on the prototype, without the composite rules the defect-1 mutant escaped the CI seed and was caught about 1 run in 9 even at 1000; with them it fails the CI seed and seeds 1-5, each within seconds. R8 forbids only lowering the setting. | Staying at 100 makes R9's single fixed-seed draw and R10's twenty weak evidence that the new rules build the windows. 1000 is kept beside the composites because the mutants M1-M3 are the only classes measured; unnamed ones still rely on volume. |
| **D6 (complexity).** `Journal.current` compares six fields to recognize a row. | Row ids are reused (§ 2.6). | `AUTOINCREMENT` on `writeback.id` is a schema change, which the GOAL makes a stop-and-ask. |
| **R-a (reading of R1, R2).** Between the commit and the rename row, the source holds the committed bytes at the pre-rename physical path. R1 and R2 grade the state "once the queue drains", which the design meets; the regressions also assert the intermediate state, so a design that silently changes it is caught. | GOAL Q2's recommended choice (one mover). | — |
| **R-b (reading of R4).** "Exactly the entries the previous engine's catalog held" is graded as the previous engine's live catalog paths, read before any `readdir`, with every live directory listed, so a remount's `readdir` walk can adopt nothing the catalog does not hold; the model compares the remount against the oracle, which the previous engine's catalog matches by parity. Tombstones are not entries a user can list. | An earlier draft compared against the previous engine's own `readdir` view, which adopts orphans in unlisted directories; the review showed it bent R4 (§ 4.1). | — |
| **R-c (reading of R9, R10).** The gate runs the GOAL's commands with flags that change no test outcome: `--frozen`, `-p no:cacheprovider`, `-rfEs`, `--junitxml` (cycle 1's precedent); R10's twenty runs go two at a time on two workers, each worker with its own scratch tree from `git archive HEAD` and its own environment, ten seeds each, with any `.hypothesis/` moved aside before every run and every CI variable Hypothesis reads removed. Counts come from JUnit, not the exit code. | R9 and R10 counts must be read, not inferred (`AGENTS.md` § Verification); twenty serial seeds plus setup approach the 10-minute command cap. | — |
| **R-d (reading of R7).** "Advancing a single write-back scheduler poll" is one call of `WritebackScheduler.poll` on an unstarted scheduler; the model also splits a poll into the pump's scan and commit, which goes beyond R7. | § 2.6's class is reachable only with user ops between scan and commit. | — |

Every figure above is rederived from the design or measured on the prototype (§ 6), not quoted.

## 4. Rabbit holes (resolved)

- **Where the commit writes (Q2)** → at `source_path`, else T(E); the rename stays the only mover
  ([`research/01-data-commit-path.md`](research/01-data-commit-path.md) § 1-2, § 4).
- **Why `flush()` diverges, and whether current-path replay converges** → historical replay cannot
  converge on a lossy queue; current-target replay converged on every case tried, with five
  amendments the design carries (ancestor placement by entry, the strict tombstone rule, rollback,
  locking, the cost row) ([`research/02-namespace-replay.md`](research/02-namespace-replay.md)).
- **Physical-path collisions** (a renamed-away name reused; an unlinked file still sitting where a new
  one lands; the collision on P(X), not X) → enforced at the user op through `_vacate`, keyed on the
  physical target ([`research/01-data-commit-path.md`](research/01-data-commit-path.md) § 3).
- **Reaching the interleaving in the model without weakening it** → commit, poll, next-due, drain
  and invariant rules plus `max_examples` 1000; R8's static gate checks the rest
  ([`research/03-model-test.md`](research/03-model-test.md)).
- **Lock order and races** → one re-entrant mutex below the path locks; replay takes no path lock;
  the stale-snapshot class is deterministic and modelled
  ([`research/04-concurrency.md`](research/04-concurrency.md)).
- **Hypothesis seeds and CI** → any `CI` value loads the `ci` profile, whose `derandomize` beats
  `--hypothesis-seed`; with a seed the example database is neither read nor written
  ([`research/03-model-test.md`](research/03-model-test.md) § 4).
- **Stale rows with reused ids** (found by the planner's prototype, not by a brief) → two silent
  losses at `141aa86`; recognized by `Journal.current` (§ 2.6). The extended model found the data-row
  form on its first `CI=true` run once `scan_writeback`/`commit_scanned` existed.
- **Prototype** → a detached worktree (`.worktrees/deferred-rename-source-orphan-prototype`) carries the
  whole design: about 300 changed lines in `overlay.py`, small additions to
  `transfer.py`, `catalog.py` and `journal.py`, 17 new tests in `test_replay.py` and the extended model. § 6 lists
  what it passes. It is a planning artifact, not the build.
- **Adversarial design review** (trigger 3: the model, the regressions and every gate are
  gate-shaped) → see § 4.1.

### 4.1 Adversarial design review

One fresh reviewer ran on 2026-11-06 with the GOAL, this design, the drafted `replay_gate.py` and
the prototype, tasked with finding trees the gates pass while broken and sequences the design gets
wrong. It reported ten findings (three HIGH, three MEDIUM, four LOW) and reproduced five. Each is
folded; the prototype and the dry runs in § 6 carry the folds.

| # | Finding | Fold |
|---|---|---|
| 1 HIGH | The rename replay settled a row unmoved when T(E) lay inside E's own source object; `flush()` drained with the source wrong (`mkdir /e; mkdir /e/p; flush; mkdir /x; rename /e→/x/e; rename /x/e/p→/p; rename /x→/p/x`). | § 2.2's inner-target rule; `DESIGN_TESTS`; mutant M5. |
| 2 HIGH | `model` was green on a tree with defect 1 back in (M1): the baseline failed on the new invariant's stray-directory catch, not on defect 1, and the model caught M1 about 1 run in 9. | The `mutants` mode (M1-M3 must fail the model at the CI seed); two composite rules (§ 2.7), after which M1 and M3 fail the CI seed and M1 fails seeds 1-5. |
| 3 HIGH | `assert_converged` compared the source against a `readdir` view that adopts orphans in unlisted directories, so a reverted tombstone rule (M4) passed every test; R-b bent R4 the same way. | Catalog-anchored `assert_converged` and `walk` (§ 2.8); `test_flush_purges_only_the_tombstone_a_rename_left`; R-b reworded. |
| 4 MEDIUM | Six edits evaded R8's static check (a flush called through `quiesce_and_flush`; a decoy `settings()`; reassigning `self.oracle` in teardown; rebinding `self.parity`; `setattr`; an autouse fixture in an unchecked conftest). | `model` now reads the settings from the `TestOverlayModel.settings` assignment, forbids any name containing `flush` in the drain rule, added assignments to the model's state, rebinding a method, `setattr`/`delattr`/`vars`/`__dict__` and statements after a `return`, allows teardown additions only after its kept statements, and pins both conftests whole. |
| 5 MEDIUM | `commit`/`replay` showed only "fails on the baseline", which an early design-specific assertion satisfies while the end-state checks are deleted. | Each named test must walk and end in `assert_converged`, whose required checks are verified; the mutants show the regressions bite on the fix's own decisions. |
| 6 MEDIUM | `records` checked a literal `resolved, Phase 8`, beaten by a rewrap or `(resolved)`; tolerance wording was narrow; nothing tied the records to the fix's diff. | Whitespace-normalized entry; its status parenthetical must name what stays open and not claim Phase 8; wider tolerance wording; the branch diff must carry `overlay.py`, `AGENTS.md`, `PLAN.md`, the new seed and `ROADMAP.md` together. |
| 7 LOW | `seeds` dropped only `CI`; any of Hypothesis's other CI variables makes twenty identical runs; a dirty `src/` let `seeds` and `suite` grade different code. | Every CI variable dropped and `is_in_ci()` asserted false in each scratch tree; the whole tree must be committed. |
| 8 LOW | Figures drifted (test counts after the two new-file tests moved to P1; D5's detection figure; R-c's "own tree per run"). | Counts, D5 and R-c rederived (§ 2.8, § 3). |
| 9 LOW | `copy`'s intent detail did not record `dest_path` in the prototype; the `meta` rule, `_purge_source`'s rollback and the in-confirm `source_path` have no test. | `dest_path` in the intent detail stays a P2 checklist item. A `meta` regression was tried and passes on the baseline too (`flush`'s ordering hides it), so it was not kept; the three stay reasoned (§ 5). |
| 10 LOW | `ci` cannot see that test_model itself passed, only that the count matches. | `ci` also requires `ci.yml` at the pushed commit to equal `main`'s, so no deselection or tolerance can enter there, and the count ties to `AGENTS.md`'s collected count, which `suite` checks on the same tree. |

The reviewer's wider fuzz (two- and three-tier stacks, `chmod`, fsync, evict, the scan/commit split,
flush and drain checks, remount; 6-, 8- and 14-path topologies) found nothing beyond finding 1, with
the same limited reach as the model.

## 5. Risks & open questions

- **Foreground latency (D4).** A FUSE `create`/`mkdir`/`rename` can wait for a whole source commit.
  Ruled at the PLAN/TECH sign-off (2026-11-06): accepted as shipped; P5 seeds the staged-publish form as
  `issues/source-commit-staged-publish.md`.
- **Foreground replay (D1).** A collision replays a queue prefix synchronously, S3 copies included.
  Bounded by the queue's length; the common case (no collision) replays nothing.
- **Threads are untested.** The suite is single-threaded; the mutex's correctness under concurrent
  FUSE callbacks, the pump and eviction is argued (§ 2.5, research 04) and exercised only through the
  deterministic stale-snapshot regressions and the model's scan/commit split. The thread-level test
  goes to `issues/freethreaded-concurrency-stress.md`.
- **Removals take no source mutex** (review cycle 1, 2026-11-06). Settling is exact (§ 2.6 amendment);
  a removal racing a replayed `mkdir` mid-act is open and pre-existing, as is queue-row identity being by
  value rather than a unique key. Both go to `issues/freethreaded-concurrency-stress.md` (R9).
- **Path locks still do not cover descendants** (research 04 § 1, pre-existing): a directory rename
  holds `lock(old, new)` while another thread works on a child. Not widened by this design and not
  fixed by it; it belongs with the thread-level seed above.
- **Model reach is probabilistic.** R9 is one fixed draw and R10 twenty; the deterministic regressions
  are the guarantee for each named class, the model the net for the ones not yet named.
- **`verify` slows to about 33 s** (D5). Accepted at sign-off (2026-11-06).
- **Reasoned, not tested** (§ 4.1, 9): `meta` applied only at a non-`NULL` `source_path` (the scheduler
  and `flush` both order the rename row first, so no test found a sequence where the old rule writes
  another object's mode); `_purge_source`'s rollback on any exception; the `source_path` update inside
  the copy's confirming transaction (a crash between two transactions; the fault-injection suite is
  `issues/crash-fault-injection-suite.md`).
- **S3 source.** The design adds no S3 call the suite exercises; same-path renames are skipped, and
  a rename stays copy-plus-delete server-side (no client egress). The integration lane that would
  exercise S3 is `issues/ci-integration-lane.md`.
- **The landed toolchain repair's gate keeps `CI_TOLERATED`** in `spec/toolchain-repair/verify/`.
  That directory is a retained record of a landed cycle, so this plan does not edit it; the
  tolerance is retired by this cycle's gates and live text. Confirmed at sign-off (2026-11-06).

## 6. Verification strategy

Engine-level throughout: no CLI surface changes, so no phase drives `tools/temp_site.sh`. Each phase
runs one mode of [`verify/replay_gate.py`](verify/replay_gate.py) (`uv run --no-project --python 3.15
python -I spec/deferred-rename-source-orphan/verify/replay_gate.py MODE…`):

| Phase | Mode(s) | Asserts |
|---|---|---|
| P1 | `replay` | each named test walks and ends in `assert_converged`, whose checks are present; the four baseline replay tests, the eleven `REPLAY_TESTS` and the one `DESIGN_TESTS` pass on this tree; each `REPLAY_TESTS` entry fails on the `141aa86` engine for cause |
| P2 | `commit` | the same shape checks; the whole `test_replay.py` passes; each of the five `COMMIT_TESTS` fails on the `141aa86` engine for cause |
| P3 | `model mutants` | R8's static comparison with `141aa86` (rules, strategies, `parity`, `real`, `snapshot`, `__init__` and `teardown` statements in order, settings floors, no excluding construct, no patching, no added handler, `conftest` helpers); test_model passes under `CI=true`; it fails under `CI=true` on the `141aa86` engine with a falsifying example (shrinking off for that run only); `mutants`: M1-M5 (§ 2.8) each fail their named regressions, and M1-M3 fail test_model under `CI=true` |
| P4 | `suite seeds` | R9: `uv sync --locked`, `tools/lint.sh` exits 0, `-n auto -m unit` under `CI=true` has 0 failed and 0 errors, at least 449 passed, test_model and every regression passed, and the count equals `AGENTS.md`'s; R10: seeds 1-20 pass from HEAD with no `.hypothesis/` |
| P5 | `ci ledger records` | R11 from the saved run record; the deferral ledger; R12 and the records |

`prepush` (clean tree, `suite`, `records`, no home-directory path in the branch diff or messages) is
P5 Run A's check before the push.

**Dry runs** (2026-11-06, development Mac, inside the agent sandbox with PyPI reachable). The
prototype's final state was cut back into one tree per phase in a second detached worktree, each cut a
commit on the one before, carrying that phase's own edits (P4's `AGENTS.md` counts; P5's records, new
seed, concurrency-seed R and `ROADMAP.md` entry), with the current gate copied in before every run:

| Tree | Mode(s) | Result |
|---|---|---|
| P1 cut | `replay` | green; `commit` red on the same tree (its tests are absent), and P2's five tests fail on the P1 engine, so P2 has something to bite |
| P2 cut | `commit` | green; `model` red on the same tree (no new rules yet) |
| P3 cut (the full prototype) | `model mutants` | green in about 2 min: static check, pass under `CI=true`, baseline failure, M1-M5 |
| P4 cut | `suite seeds` | green in about 6 min: lint, 466 passed under `CI=true` (about 33 s serial), count equal to `AGENTS.md`'s, seeds 1-20 on two workers |
| P5 cut | `ledger records prepush` | green in about 30 s |
| P5 cut | `ci` against fixtures built from the previous cycle's saved run record (42147199752) | green on the success-shaped fixture; red on the real record (test_model failing), a Linux count of 449, a Linux skip, a macOS count one short, and a run for another commit |

The prototype's full suite ran once on the P4 cut (466 passed, 0 failed). Before the review's folds the
first prototype was already green on `replay`, `commit`, `model` and `seeds`, and its full suite passed
461; the folds added the inner-target rule, the catalog-anchored convergence check, the composite rules
and five tests.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
