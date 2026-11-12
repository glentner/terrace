---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A write racing a commit can be recorded clean and never reach the source

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`, and
> that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped` means
> none of that has happened yet.

## Problem

`_commit_hop` decides that a copy is clean from two signals, and a write can slip past both. After
the copy it resets `marked_dirty` on every open handle, stats the holder, and marks the residency
clean when size and mtime equal what it copied (`src/terrace/engine/overlay.py:992-1007`). A writer
takes no lock once its handle is marked: `write` reads `handle.marked_dirty` and then calls
`os.pwrite` (`overlay.py:793-796`). The committer holds the path lock; the writer does not need it.

A copy recorded clean with bytes its upstream lacks is the failure `AGENTS.md` § 7 names: eviction
drops clean copies, so the newer bytes are deleted on the next quota crossing, and until then nothing
queues them. No error is raised and both queues read empty.

**Route 1: the flag is read before the reset and the write lands after the stat.** The writer reads
`marked_dirty == True` and skips the mark. The committer resets the flag, stats (unchanged), and
records clean. The writer's `pwrite` lands. If that was the handle's last write, `release` records the
new size and mtime and leaves the residency clean. Reproduced at `64faf27` on one thread by injection:
the holder tier's `stat` is wrapped so that, on the commit's post-copy stat, a `pwrite` through the
open handle follows the real stat. Two-tier POSIX stack, tmpdir tiers, no mount, Python 3.15.2 on
macOS:

```
create /f.dat ; open ; write 'AAAA' ; commit_upstream (injected pwrite 'BBBB' after the post-copy stat)
release ; flush
  residency = {project: clean, scratch: clean}   queued = 0
  stage bytes = 'BBBB'   source bytes = 'AAAA'
evict_copy -> True ; read back = 'AAAA'
```

The window is the stat call, and the writer must sit between its check and its `pwrite` for that long,
so the route needs a preempted writer. It is narrow and it is not zero, on either interpreter build.

**Route 2: the write changes neither size nor mtime.** A write that lands after the copy has read its
range, of the same length, at a timestamp equal to the previous write's, leaves the stat identical. The
flag does not catch it either: it was already set, and the reset comes after. Reproduced at `64faf27`
by injection, same stack: `TransferPool.copy` is wrapped so that a real `engine.write` of the same
size follows the copy and the holder's mtime is set back to its pre-copy value.

```
write 'AAAA' ; commit_upstream (injected engine.write 'BBBB' after the copy, mtime restored)
  residency = {project: clean, scratch: clean}   queued = 0
  stage bytes = 'BBBB'   source bytes = 'AAAA'
```

Whether a real filesystem produces equal timestamps is **unverified here and decides how wide this
route is**. On the development Mac it does not: 0 of 2,000 back-to-back same-size writes left
`st_mtime_ns` unchanged. Linux filesystems have historically stamped mtime from the kernel's coarse
clock, which advances once per timer tick, so two writes inside one tick share a timestamp; a small
file copies in less than a tick. If the target kernels behave that way, an in-place rewrite during a
commit is the ordinary case for a `passthrough` boundary under a busy writer, and this is the workload
`GOAL.md` names ("heavy writes in the middle of a file"). Measure it on the deployment kernel and on
each filesystem a tier may sit on before sizing the fix.

`issues/freethreaded-concurrency-stress.md` records the handle fields as unguarded and calls the
effect benign, "one redundant journal round trip". That reading covers a lost set of the flag. It does
not cover a stale read of it, which is Route 1.

## Why it was deferred

Not deferred: found by an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
Pre-existing; the lines blame to the Phase 4 data path (`4aea975`).

## Outcome / vision

A copy goes clean only when the engine knows that no write has landed since the bytes it copied were
read, by its own count and not by what a filesystem's timestamps resolve. The rule is one the engine
can state without naming a filesystem.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion.

- **R1** — WHEN a write through an open handle lands at any point between a commit's read of the
  holder's bytes and its recording, the holder SHALL stay dirty and its queue row SHALL stay queued,
  whatever the write's size and whatever timestamp the tier's filesystem gives it.
- **R2** — WHEN no write lands in that interval, the commit SHALL record clean and settle the row, as
  today.
- **R3** — Both reproductions above SHALL be tests, and each SHALL fail on `64faf27`.
- **R4** — THE interval's two ends SHALL be decided by state the engine owns (a per-entry write count
  read before the copy and compared after it, under a lock the writer's increment also takes, is one
  shape) and the stat comparison SHALL NOT be what clears a copy.
- **R5** — A writer on an already-dirty handle SHALL pay no SQLite statement per write, as today.

## Notes

- The copy's lifecycle is a state machine nobody wrote down. Its state is spread over
  `residency.state`, the `data` row, an intent row, `Handle.marked_dirty` and `Handle.written`, and
  its transitions sit in `create`, `TransferPool.copy`, `Journal.mark_dirty`, `_commit_hop`,
  `commit_upstream`, `evict_copy`, `_remove`, `truncate` and `_record`. Declaring it (absent, clean,
  dirty, committing, evicting; who may move it and under which lock) is most of this fix.
- `writeback.state` admits `'active'` (`src/terrace/state/schema.sql:112`) and nothing ever sets it. A
  commit in flight is the state the vocabulary was reserved for.
- A handle-less `truncate` and `_record` write the same rows and belong in the same table.
- `mmap` writes reach the engine as ordinary `write` callbacks, so one counter covers them.
- Related: `AGENTS.md` § *Invariants* § 6 ("After a copy, `changed` detection re-arms rather than
  settles"), § 7; `issues/freethreaded-concurrency-stress.md` (the handle fields);
  `issues/pump-liveness-and-loss-windows.md`.
- Found by: an out-of-cycle review of the whole tree at `64faf27` (2026-11-11).
