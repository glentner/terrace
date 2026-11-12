---
status: unshaped
kind: perf
appetite: small
lane: public
---

# Opening a resident file costs three database writes

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

A read of a file that is already on its fastest tier writes to SQLite three times. `open` advances
the copy's LRU clock (`catalog.touch`, `src/sosfs/engine/overlay.py:699`), and `release` runs
`_record`, which updates the residency row and adjusts the tier's usage counter by a delta of zero
inside one transaction (`overlay.py:915-922`). SQLite admits one writer at a time, so every open and
every close in the mount passes through one lock, whatever the thread count and whichever interpreter
build runs it.

Measured at `64faf27` through the engine API, with no FUSE in the path: a two-tier POSIX stack on
tmpdir tiers, 2,000 files of 4 KiB, Python 3.15.2 (GIL enabled) on an Apple-silicon Mac, one run.
"Cold" is the first open, which promotes the file from the source; "warm" is open, one 4 KiB `pread`,
release, of a file already staged. SQL writes were counted by a connection subclass that counts
`INSERT`, `UPDATE` and `DELETE` statements.

| Case | Files per second | SQL writes per file |
|---|---|---|
| Cold open (promote) | 1,107 | not counted |
| Warm, 1 thread | 3,144 | 3.0 |
| Warm, 4 threads | 4,251 | 3.0 |
| Warm, 16 threads | 3,621 | 3.0 |
| `os.open`, `os.pread`, `os.close` on the stage copy directly | 35,880 | 0 |

One run on a laptop is an indication, not a benchmark. Two things in it are structural and will hold
on any host: the count of three, and the flat curve past four threads. The engine is roughly a tenth
the speed of the tier it serves before a FUSE round trip is added, and more reader threads do not
help.

This is the flagship workload's hot path. A dataset of a hundred million small files
(`issues/sharded-archive-sources.md`) is opened once per file per epoch. At the warm rate above one
epoch's opens take about seven hours of engine time; a training job reads thousands of samples a
second.

`PLAN.md` § *Risks and mitigations* names the hazard and its mitigation: "SQLite contention under
heavy metadata load — WAL + per-thread connections + batched dirty-flag updates". The batching is not
implemented for the LRU clock, and it is the one figure in the catalog that is safe to lose: after a
crash a stale clock costs a worse eviction order and nothing else.

Two more costs on the same path, read from the code and not measured:

- **The scheduler reads the whole queue every tick.** `WritebackScheduler.due` fetches every queued
  row and computes deadlines in Python (`src/sosfs/engine/writeback.py:85-88`), once a second, though
  `writeback_boundary` indexes `(to_tier, state, last_write)`.
- **Each promotion fsyncs.** `PosixTier.put` fsyncs every file it writes, a stage copy of a clean file
  included. A promoted copy that is lost in a crash is re-promoted from its upstream, so the fsync
  buys nothing there; it is what makes a commit to the source durable.

## Why it was deferred

Not deferred: found by an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
`issues/benchmark-harness.md` holds measurement until the engine stops moving; this figure is the
argument for taking a baseline first.

## Outcome / vision

Reading a resident file changes no row on the calling thread. The LRU clock is kept in memory and
written in batches, and a close that changed nothing writes nothing. Reader threads scale until the
tier, not the catalog, is the limit.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a resident file is opened read-only, read and released, the calling thread SHALL
  execute no SQL write statement, asserted by a count and not by wall time.
- **R2** — THE LRU clocks SHALL reach the catalog within a bounded interval and at shutdown, and a
  test with an injected clock SHALL show eviction order follows them.
- **R3** — WHEN a handle that wrote nothing is released, no residency row and no usage counter SHALL
  be written.
- **R4** — WHEN the process is killed between flushes, the next mount SHALL start, and the only effect
  SHALL be LRU clocks older than the truth.
- **R5** — A baseline of the table above SHALL be re-measured before and after, on one named host,
  with the method recorded beside the numbers.

## Notes

- **A larger question this does not settle.** Serving a hundred million members by promoting each one
  to its own file costs a promotion, two transactions and an inode per member. A member of a staged,
  uncompressed tar can be served by `pread` on the tar at the member's offset, which the persisted
  index already records, with no promotion and no inode. `issues/sharded-archive-sources.md` owns that
  choice.
- The kernel can cache attributes and directory entries so that repeated `getattr` calls never reach
  the daemon. The mount passes no cache timeouts (`src/sosfs/fuse/mount.py:310`); what is safe to
  cache follows from the single-mount rule and is worth one criterion at shaping.
- `PLAN.md` § *Future work* lists a kernel-passthrough adapter. With read-only handles on resident
  copies it would take the daemon out of `read` entirely; the binding in use does not expose it.
- Related: `issues/benchmark-harness.md`, `issues/read-only-mount.md`,
  `issues/pump-liveness-and-loss-windows.md` (the scheduler), `issues/transfer-priority-lane.md`.
- Found by: an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
