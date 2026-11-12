---
status: unshaped
kind: refactor
appetite: big
lane: public
---

# Rebuild how the namespace reaches the source

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

Seven fix cycles in a row worked on one mechanism, and each found the next defect in it. Since the
factory was adopted (`6a046b4`, 2026-11-04) six landed cycles and two parked ones belong to deferred
namespace replay: `deferred-rename-source-orphan`, `source-path-truncate-and-stat`,
`source-served-open-race`, `truncate-recording-after-ancestor-rename`, `entry-identity-token`,
`nested-rmdir-replay-recursion`, and the parked `rename-over-tombstoned-child` and
`model-commit-route-reach`. Measured with `git diff --shortstat 6a046b4 64faf27`:

| Tree | Change |
|---|---|
| `src/` | 727 insertions, 192 deletions, 5 files; `engine/overlay.py` grew from 1,036 to 1,501 lines |
| `tests/` | 3,281 insertions, 49 of them the toolchain repair's; `tests/engine/test_replay.py` grew from 88 to 3,098 lines |
| `spec/` | 21,143 insertions, 19,176 of them these cycles' records |

No roadmap entry outside this mechanism moved in that time except the toolchain repair. The cycles
were correct one by one. The direction is the finding: three properties of the design generate these
defects, and each cycle closed one symptom by adding a mechanism instead of removing a cause.

**1. Nothing has a stable identity.** The catalog, the path locks and the stage tiers are all keyed by
logical path, and row ids are reused: `entry.id`, `writeback.id` and `intent.id` are plain
`INTEGER PRIMARY KEY` (`src/sosfs/state/schema.sql:59`, `:106`, `:124`), so the id of a deleted
highest row goes to the next insert. Two cycles built machinery around that instead of ending it:

- `Journal.current` recognizes a queue row by six fields, and `_replay_through` by a four-field mark
  (`src/sosfs/state/journal.py:210-226`, `src/sosfs/engine/overlay.py:1319-1321`);
- the purge watch (`PurgeWatch`, `_watch`, `_named`, `_adopt`'s clearing, a thread-local, a fifth lock
  level; `overlay.py:246-285`, `:748-780`, `:1477-1501`) exists only to tell an entry from a file that
  took its id.

`AUTOINCREMENT` on the three columns ends id reuse. It was turned down twice as "a schema change with
no migration path" (`spec/deferred-rename-source-orphan/PLAN.md` D6;
`spec/entry-identity-token/GOAL.md` Q1, ruled 2026-11-09). The premise does not hold yet: the
repository is private, unreleased at `0.1.0.dev0`, and has never run on a cluster, so no state database
exists that a version bump would strand. That stops being true at the first deployment.

**2. Tombstones live in the logical namespace.** A removed entry keeps its row at its old logical
path, and a rename inserts a second row at the old path to mask the source object until the move
commits (`overlay.py:489-493`). `entry.path` is `UNIQUE`, so every such row blocks the name it sits on.
The mask has one exit, the rename row's own commit (`overlay.py:1193-1200`); a removal cancels that row
(`_remove`, `overlay.py:562`), and the mask then outlives everything. Reproduced at `64faf27` on a
two-tier POSIX stack with tmpdir tiers and no mount, six engine calls after a `readdir`:

```
rename /d2/a.dat -> /a.dat ; unlink /a.dat ; flush
  queued=0 pending=0   row at /d2/a.dat = {deleted: True, source_path: None}
mkdir /new ; create /new/a.dat ; rmdir /d2 ; rename /new -> /d2
  sqlite3.IntegrityError: UNIQUE constraint failed: entry.path      (not an OSError)
```

That is the parked `rename-over-tombstoned-child` defect, and the row is permanent: a drain does not
clear it and neither does a remount. What masks a source object is a fact about a physical place
(`source_path`), which already has its own index (`entry_source_path`) and its own lookup
(`Catalog.holder`). It does not need a row in the logical tree.

**3. Path locks cover one path, and a directory rename moves many.** A lock on `/a/b/c` does not stop
a rename of `/a`, which rewrites every descendant's catalog path and moves every stage copy. Five
cycles met this and each deferred it to `issues/freethreaded-concurrency-stress.md`, a `test` seed,
which now records four sequences reproduced by injection, two of them silent (an `open` that returns
another entry's stage bytes; a promotion that copies the source over a dirty stage copy and records it
clean, so the write is gone), and a list of eleven calls an ancestor's catalog move can land inside. The `open`, `_serve` and
`truncate` docstrings state the windows they leave open in their own words. The response each time
was to widen one global lock: `_source_lock` is now held across whole-file copies to and from the
source, across a rename's catalog transaction, and across every handle-less `truncate`
(`AGENTS.md` § 10).

**The replay itself is a dependency solver written as a queue walker.** The queue stopped being an
operation log in the first cycle: rows resolve "the entry's current place, never the paths a row
recorded" (`AGENTS.md` § 5). What remains is a set of entries whose source place differs from their
target, and `_replay_through` (`overlay.py:1286-1404`) discovers the order to fix them at run time,
with frames, a stack, a waiting table, marks and a stuck test. Its cost is super-linear on some shapes
(`issues/replay-cost-at-scale.md`: 1,097 s for one drain) and mutants of its branches survive the
suite (`issues/replay-defensive-branch-coverage.md`).

**The source the project is for cannot take a replayed operation at all.** The maintainer's statement
of 2026-11-11: what sets the project apart is write-back with delayed re-aggregation into compressed
bundles at the source. A bundle has no rename, no unlink and no mkdir. The engine as built would
replay each queued row into `ArchiveTier`, and the adapter answers every one by rewriting the whole
working archive and reading every member back into the index (`_repack`,
`src/sosfs/tier/archive.py:529`, from six callers at `:594` to `:677`). `resync`, the one call that
pushes a bundle to its source, has no caller under `engine/`. The model that fits a bundle is
state-based: a change marks its bundle dirty, and on the bundle's clock or at a drain the engine
rebuilds it from the catalog and the staged bytes and replaces it with one atomic write. That rebuild
sets every member's source place and drops every removed entry in the bundle in one transaction, so
it has no order to discover. Decision 3 below is not open for a bundle source, and a bundle source is
the case to build it on first.

## Why it was deferred

Each cycle was a `fix` with a small appetite and a non-goal against schema changes, and the blind
review grades a diff against the invariants as written, so no step was placed to question the
invariants. `PLAN.md` names the alternative and sets it aside: "Full catalog↔source reconciliation is
deliberately *not* adopted on the flush path" (§ *Overlay engine* → Deferred-op replay), and its phase
outline offered "source-path tracking or flush-time reconciliation" as the two candidates. This seed
reopens that choice with seven cycles of evidence. Pre-existing: every property above predates the
factory.

## Outcome / vision

The namespace core is small enough to hold in one reading, and its lifecycles are declared instead of
inferred. A reader finds, in one module each: the states an entry can be in and the transitions between
them; what a queued row is and what ends it; how the engine decides which source operations a drift
needs and in what order. An id names one thing for the life of a state database. A directory rename
cannot change what a call in flight is acting on. The stateful model and the oracle in
`tests/engine/test_model.py` judge the result, with the wider reach the parked
`model-commit-route-reach` cycle prototyped.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. The first is a design decision and belongs in `PLAN.md`
before any code, under the plan-drift rule.

- **R1** — `PLAN.md` SHALL record, with the maintainer, the decision on each of these, and the
  prototype evidence for it:
  1. **Stable ids.** `AUTOINCREMENT` on `entry.id`, `writeback.id` and `intent.id`, as a schema version
     bump taken before any state database is deployed.
  2. **Where tombstones live.** Out of the logical namespace: a removed or moved-away entry holds a
     physical place only, and the name it left is free at once.
  3. **How the source catches up.** From state, at a unit the source type sets. For a bundle source
     the unit is the bundle: dirty on any change inside it, rebuilt whole on its clock. For a POSIX or
     object-prefix source the unit is the entry: a planner reads the drift (target against
     `source_path`, and removed entries still holding a place) and emits an ordered list of source
     operations, as a pure function testable without tiers. Write-through is that planner run at once
     on each call, and may be the right default for a source that takes namespace operations cheaply.
  4. **What excludes a directory rename.** A namespace lock that per-file calls share and a directory
     rename takes alone; or stage copies addressed by entry id, so a rename moves no stage object.
     The second gives up stage trees a person can read by name, which matters when a state database is
     lost.
- **R2** — WHEN an entry is purged, its id SHALL never name another entry in the same state database.
- **R3** — WHEN a removed or moved-away entry's former name is taken by a create, a mkdir or a rename
  at any depth, the call SHALL succeed as it does on a plain directory. The reproduction above SHALL
  pass, and so SHALL the cases the parked `rename-over-tombstoned-child` plan collected.
- **R4** — WHEN a directory is renamed while another call acts on an entry below it, that call SHALL
  complete against the entry it named or raise an `OSError`, and SHALL never read, write or record
  another entry's bytes. The sequences `issues/freethreaded-concurrency-stress.md` records as reproduced
  by injection SHALL be tests that pass.
- **R5** — THE states of an entry, of a queued row and of a source synchronization SHALL each be an
  enumeration with a transition table in one module, and every site that changes one SHALL go through
  it. A test SHALL enumerate each table.
- **R6** — WHEN a queue of N namespace changes drains, the number of catalog and queue reads SHALL grow
  linearly in N for the shapes `issues/replay-cost-at-scale.md` measured.
- **R7** — THE stateful model SHALL pass with the wider op mix the parked `model-commit-route-reach`
  cycle prototyped, on the fixed CI draw and on random seeds, and every divergence class
  `tests/engine/test_replay.py` lists SHALL still converge.
- **R8** — `AGENTS.md` § 5 and § 10 SHALL state properties a test checks (one holder per source place;
  the source equals the overlay view once the queue drains; the lock order) and SHALL NOT narrate the
  mechanism. `PLAN.md` § *Risks* SHALL lose its account of the replay cycles.

## Notes

- **What this absorbs, if adopted.** The parked cycles `rename-over-tombstoned-child` (branch
  `fix/rename-over-tombstoned-child`, its prototype and probes in a local ref) and
  `model-commit-route-reach` (branch `test/model-commit-route-reach`) become acceptance material here
  rather than separate landings: R3 and R7. `issues/replay-cost-at-scale.md` and
  `issues/replay-defensive-branch-coverage.md` describe a function this would delete: R6 carries the
  first, and the second has no object once `_replay_through` is gone.
  `issues/source-commit-staged-publish.md` depends on decision 4. None of those seeds is retired by
  this one being filed; that needs the maintainer's word.
- **Two stages.** The model, the schema and the bundle source first, where there is no replay to
  port. POSIX and object-prefix sources second, where `_replay_through` is deleted. Shaping decides
  whether that is one cycle or two; the first stage is what the bundle loop waits on
  ([`issues/archive-finalization-integration.md`](archive-finalization-integration.md)).
- **What it does not touch.** The data path's clean transition
  (`issues/racing-write-marked-clean.md`), the pumps, drain and rescue, and the tier adapters.
- **The test asset carries over.** The model compares the engine with a plain directory, so it judges
  any mechanism. Tests in `tests/engine/test_replay.py` that assert an outcome (the source equals the
  oracle, one holder per place) carry over; those that inject at a named internal step do not, and
  shaping should count each kind before the appetite is agreed.
- **Order with the rename.** `issues/rename-to-terrace.md` changes the marker directory and this
  changes the schema version. Both break the on-disk format, and both are free only until something is
  deployed.
- Open question — **is deferral wanted for a POSIX source at all?** `GOAL.md` asks for delayed
  write-back of data, to bound write amplification. A rename or a mkdir is not amplified by repetition.
  For an object-store source a directory rename is one copy per object, which is the case for keeping
  deferral.
- Related: `spec/deferred-rename-source-orphan/`, `spec/entry-identity-token/`,
  `spec/nested-rmdir-replay-recursion/`; `AGENTS.md` § *Invariants* § 5, § 10; `PLAN.md` § *Overlay
  engine* → Deferred-op replay, § *Risks and mitigations*.
- Found by: an out-of-cycle review of the whole tree at `64faf27` (2026-11-11). The reproduction was
  run with Python 3.15.2 on macOS against tmpdir tiers through the engine API.
