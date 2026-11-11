# 02 — The worklist replay, and why one pass drains (Q4)

Planner, 2026-11-10. This brief describes the design in PLAN § 2 as the planning prototype runs it.

## The loop

`_replay_through(rows, row=None)` replaces the recursion with a loop over a list of frames.

- **A frame** is a queued row on the replay's stack, or, at the root of a `_vacate` or `_purge_source`
  replay, just the set of rows that replay needs. Each frame holds:
  - its row;
  - the rows its prefix has yet to reach (`remaining`);
  - its cursor into the queue (`at`);
  - the needs it read when its prefix last ran (`needs`).
- **The stack** is the set of the frames' row ids. `done` holds the rows this replay ran or found gone.
- **A frame that is running its prefix** advances its cursor in queue order. It skips data rows and rows
  on the stack, and discards each row it reaches from `remaining`. It pushes the first row that is not
  done and not memoized as waiting. When `remaining` empties, or the queue ends with nothing queued after
  it, the frame stops scanning.
- **A row frame that is not scanning** reads, in order:
  - whether its row is still queued (gone: pop, done);
  - its entry;
  - its needs. An `rmdir` needs the holders below its directory's object (`_holder_rows`). A rename
    whose target lies inside its own object needs the places between (`_clear_inner_target`).
- **What it does with its needs:**
  - It waits (pop, row stays queued, memo noted) if they meet the stack, or if a pass made no progress
    and it is not at the bottom.
  - At the bottom, with no progress, an `rmdir` acts and a rename raises.
  - With needs left, it restarts its prefix: `remaining = needs`, `at = 0`.
  - Otherwise it acts (`_execute_namespace_locked`), pops, and is done.
- **The queue** is read once, at the first needs read or scan. After that, before each needs read and when a
  scan reaches the end, `Journal.queued_marks` returns the identity of every queued row, `(id, entry, op,
  first_dirty)`, without decoding any, and the rows not seen yet are added. A racing removal's row is found
  this way whatever its id (ids are reused) and wherever it sorts (the clock may step back).
- **Marks, not ids.** The stack, `done`, the memo and each frame's sets hold marks, never bare ids
  (review 2, F1).
- **The memo:** a row that waited is skipped while no row has acted since and a row it waited on is still
  on the stack. In that state its needs read would come out the same and it would wait again.

The replay never calls itself or `_execute_namespace`. Its depth is bounded by the number of queued
rows, since a row is on the stack at most once.

## Q4: one pass leaves none of the rows it read queued

1. **A row never needs a row of its own entry.** `_holder_rows(…, exclude=entry.id)`, and
   `_clear_inner_target` skips `other.entry == entry.id`.
2. **The bottom row never waits.** At the bottom, the stack is that row alone. By (1) its needs never
   meet it, so it never waits on the stack. The no-progress wait applies only above the bottom.
3. **At the bottom, every pass makes progress** while the needs relation is acyclic. A need N that the
   bottom row B's own scan pushes has the stack {B, N}. N waits on B only if each of N and B is held
   strictly inside the other's place, which is impossible. So N acts.

   A need pushed deeper (inside another row M's prefix) may wait on M. In that case N sits after M in
   queue order: B's scan would have pushed N directly had N come first. N is still in B's `remaining`,
   since only the frame that scans a row discards it. So B's scan reaches N after M pops, with M off the
   stack and the memo void, and N acts.

   The relation is acyclic at any moment because every need is held strictly inside the needing row's
   place. Each pass that makes progress settles at least one row, so the loop ends.
4. **Each driver runs every row it read at the bottom of its own replay:**
   - `flush` and `drain` call `_execute_namespace(r)` for every namespace row in their snapshot;
   - a poll does the same for every due row, in the same order, since `due` sorts stably over `queued`.

   By (2) and (3), each such call returns with r acted or gone, or raises. A row that waited inside an
   earlier row's replay sits after that row in the snapshot. A row before it was already run by the
   driver, so it cannot be queued to wait. So one pass leaves none of its rows queued. `drain`'s
   `complete=True` therefore holds without a loop.
5. **`_vacate` and `_purge_source`** push each row they reach at the bottom, so each row they need acts.
   Rows after the last one they need are not run, as at the base.
6. **Scale.** No step recurses. Each needs read is one indexed read (`Catalog.held`) plus a pass over the
   replay's queue in memory. The memo keeps a waiting row from being read again on every scan.

   Planning prototype measurements (one tier, flush alone):
   - 500 sibling chains: 0.6 s;
   - 1,000 chains: 1.3–1.8 s;
   - 1,000 two-deep chains: 3.5 s.

   Before the memo, 500 two-deep chains took 26 s.

## What changes from the base's order

Rows run in queue order, except that a row first runs the prefix through what it needs, as at the base.
A row on the stack is skipped where the base re-entered it. Re-entering never ran a row that the converging
base shapes needed: the shape map and the differential walks show no regression.

A nested row that cannot act yet stays queued where the base recursed. A row reads its needs again after
its prefix. The base read them once and, for a rename, raised: that is the R10 class.
